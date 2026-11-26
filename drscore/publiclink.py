"""Public links: one report opened by a secret URL, without signing in (docs/public-links.md).

A link shows its report exactly as one DRS user sees it (``run_as``): that user's rights, row
filters included, are checked at every use, so revoking the user's access - or disabling the user
- closes the link too. A link that is not public (``is_public`` False) is the report's own address
for signed-in readers: it opens the sign-in page first, and the report runs as the reader, with
their own rights - a reader without them is refused. A dashboard is shown for the snapshot the link's own run made, never one a
browser names. The link holds the parameter values; a reader may change only the ones listed as
editable, and those are validated like any other input. A link may also be allowed to tick one
choice list (``choice_list``): its page reads and saves that list (drscore.choicelist) - the only
thing a public reader can change.

Only the SHA-256 of the token is stored: the URL is shown once, when the link is made. A link is
never deleted, it is switched off (``revoke``), and the access log keeps who used what.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from drscore import siteurl
from drscore.audit import audit, row_snapshot
from drscore.db.models import PUBLIC_VIEWS, ChoiceList, PublicLink, Report, ReportParam, User
from drscore.db.types import utcnow
from drscore.errors import DRSError
from drscore.settings import AppSettings

TOKEN_BYTES = 32
PATH = "/p/"


class LinkError(Exception):
    """A link that cannot be made as asked."""


# One site that may frame a link: a scheme and a host (``*.`` allowed in front), an optional port;
# a path after it is dropped (frame-ancestors compares sites).
_FRAMER = re.compile(r"^(?:(https?)://)?((?:\*\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*)(:\d{1,5})?(?:/\S*)?$")
FRAMERS_MAX = 1000


def frame_ancestors(text: str | None) -> str | None:
    """The sites that may show a link in a frame, as typed (commas, spaces or lines between them):
    ``*`` for any site, or origins such as ``https://hrms.example.com/``. Returns the value of
    CSP ``frame-ancestors`` besides ``'self'`` (``*``, or origins separated by a space), None for
    this portal only. LinkError for anything else - a quote or ``;`` never reaches the header."""
    items = [i for i in re.split(r"[\s,]+", (text or "").strip().lower()) if i]
    if not items:
        return None
    kept: list[str] = []
    for item in items:
        if item == "*":
            continue
        found = _FRAMER.match(item)
        if not found:
            raise LinkError(f"frame: {item!r} is not a site (use *, or e.g. https://hrms.example.com)")
        scheme, host, port = found.groups()
        origin = (f"{scheme}://" if scheme else "") + host + (port or "")
        if origin not in kept:
            kept.append(origin)
    if "*" in items:  # any site: the others add nothing
        return "*"
    value = " ".join(kept)
    if len(value) > FRAMERS_MAX:
        raise LinkError(f"frame: at most {FRAMERS_MAX} characters")
    return value


def frame_csp(csp: str, framers: str | None) -> str:
    """A page's Content-Security-Policy with the link's other framing sites added (``*``: any)."""
    if not framers:
        return csp
    return csp.replace("frame-ancestors 'self'", "frame-ancestors " + ("*" if framers == "*" else f"'self' {framers}"))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def url_of(settings: AppSettings, token: str) -> str:
    return siteurl.portal_url(settings) + PATH + token


def _checked(session: Session, settings: AppSettings, *, report_code: str, run_as: str,
             params: dict[str, Any] | None, editable: list[str] | None, view: str,
             refresh_seconds: int | None, expires_at: datetime | None, choice_list: str | None = None,
             new_expiry: bool = True) -> tuple[Report, User, ChoiceList | None]:
    """The report and the user of a link as asked, or LinkError: what ``create`` and ``change`` both
    require of a link before it is saved. An expiry kept as it was (``new_expiry`` False) may have
    passed: the link stays closed, its other settings may still change."""
    from drscore.authz.permissions import require_report
    from drscore.render import design

    report = session.scalars(select(Report).where(Report.report_code == report_code)).first()
    if report is None or not report.is_active:
        raise LinkError(f"no active report {report_code}")
    user = session.scalars(select(User).where(User.username == run_as)).first()
    if user is None or not user.is_active:
        raise LinkError(f"no active user {run_as}")
    try:
        require_report(session, user, report_code)
    except DRSError as exc:
        raise LinkError(f"{run_as} may not view {report_code}: the link runs with that user's rights") from exc
    if view not in PUBLIC_VIEWS:
        raise LinkError("view: " + ", ".join(PUBLIC_VIEWS))
    try:
        design.check(session, report, settings, design.require_view(report, view))
    except DRSError as exc:
        raise LinkError(f"{report_code} has no usable {view} view: {exc.admin_detail or exc.message}") from exc
    names = set(session.scalars(select(ReportParam.param_name).where(
        ReportParam.report_id == report.report_id, ReportParam.is_active)))
    unknown = sorted((set(params or {}) | set(editable or [])) - names)
    if unknown:
        raise LinkError(f"{report_code} has no parameter {', '.join(unknown)}")
    if refresh_seconds is not None and not 30 <= refresh_seconds <= 86400:
        raise LinkError("refresh: 30 to 86400 seconds")
    if new_expiry and expires_at is not None and expires_at <= utcnow():
        raise LinkError("the expiry is in the past")
    chosen = None
    if choice_list:
        chosen = session.scalars(select(ChoiceList).where(ChoiceList.list_code == choice_list)).first()
        if chosen is None or not chosen.is_active:
            raise LinkError(f"no active choice list {choice_list}")
        if view != "html":
            raise LinkError("a link that ticks a choice list shows an HTML design (--view html): its page saves it")
    return report, user, chosen


def create(session: Session, actor: str, settings: AppSettings, *, report_code: str, run_as: str,
           params: dict[str, Any] | None = None, editable: list[str] | None = None, view: str = "bi",
           title: str | None = None, refresh_seconds: int | None = None,
           expires_at: datetime | None = None, choice_list: str | None = None,
           is_public: bool = True, framers: str | None = None) -> tuple[PublicLink, str]:
    """Makes a link and returns it with its token - the only time the token is known."""
    report, user, chosen = _checked(session, settings, report_code=report_code, run_as=run_as, params=params,
                                    editable=editable, view=view, refresh_seconds=refresh_seconds,
                                    expires_at=expires_at, choice_list=choice_list)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    link = PublicLink(token_hash=token_hash(token), token_hint=token[:8], report_id=report.report_id,
                      run_as_user_id=user.user_id, title=title, params_json=params or None,
                      editable_json=sorted(set(editable)) if editable else None, view_key=view,
                      refresh_seconds=refresh_seconds, expires_at=expires_at, created_by=actor[:100],
                      choice_list_id=chosen.list_id if chosen else None, is_public=is_public,
                      frame_ancestors=frame_ancestors(framers))
    session.add(link)
    session.flush()
    audit(session, actor, "PUBLICLINK_ADD", "public_link", f"{link.link_id} {link.token_hint}",
          after=row_snapshot(link) | {"token_hash": "***"})
    return link, token


KEEP: Any = object()  # ``change``: this setting stays as it is


def change(session: Session, actor: str, settings: AppSettings, ref: str, *, report_code: Any = KEEP,
           run_as: Any = KEEP, params: Any = KEEP, editable: Any = KEEP, view: Any = KEEP, title: Any = KEEP,
           refresh_seconds: Any = KEEP, expires_at: Any = KEEP, choice_list: Any = KEEP,
           is_public: Any = KEEP, framers: Any = KEEP) -> PublicLink:
    """Changes what a link shows, keeping its token: an address already handed out goes on working
    and shows the new report or view. The link is checked again as a whole, as ``create`` does.
    None clears a setting (params, editable, title, refresh, expiry, framing sites)."""
    link = find(session, ref)
    if not link.is_active:
        raise LinkError(f"link {ref} is switched off; make a new one")
    before = row_snapshot(link)
    want = {
        "report_code": link.report.report_code if report_code is KEEP else report_code,
        "run_as": link.run_as.username if run_as is KEEP else run_as,
        "params": (link.params_json or None) if params is KEEP else (params or None),
        "editable": (link.editable_json or None) if editable is KEEP else (editable or None),
        "view": link.view_key if view is KEEP else view,
        "refresh_seconds": link.refresh_seconds if refresh_seconds is KEEP else refresh_seconds,
        "expires_at": link.expires_at if expires_at is KEEP else expires_at,
        "choice_list": ((link.choice_list.list_code if link.choice_list else None) if choice_list is KEEP
                        else (choice_list or None)),
    }
    report, user, chosen = _checked(session, settings, **want, new_expiry=expires_at is not KEEP)
    if framers is not KEEP:
        link.frame_ancestors = frame_ancestors(framers)
    link.report_id, link.run_as_user_id = report.report_id, user.user_id
    link.choice_list_id = chosen.list_id if chosen else None
    link.params_json = want["params"]
    link.editable_json = sorted(set(want["editable"])) if want["editable"] else None
    link.view_key, link.refresh_seconds, link.expires_at = want["view"], want["refresh_seconds"], want["expires_at"]
    if title is not KEEP:
        link.title = title or None
    if is_public is not KEEP:
        link.is_public = bool(is_public)
    session.flush()
    session.expire(link, ["report", "run_as", "choice_list"])
    audit(session, actor, "PUBLICLINK_SET", "public_link", f"{link.link_id} {link.token_hint}",
          before=before | {"token_hash": "***"}, after=row_snapshot(link) | {"token_hash": "***"})
    return link


def find(session: Session, ref: str) -> PublicLink:
    """A link by its id or the first characters of its token (as `publiclink list` shows them)."""
    query = select(PublicLink)
    query = query.where(PublicLink.link_id == int(ref)) if ref.isdigit() else query.where(
        PublicLink.token_hint.startswith(ref[:8]))
    found = list(session.scalars(query))
    if len(found) != 1:
        raise LinkError(f"{'no' if not found else 'more than one'} link {ref}")
    return found[0]


def revoke(session: Session, actor: str, ref: str) -> PublicLink:
    link = find(session, ref)
    before = row_snapshot(link)
    link.is_active = False
    audit(session, actor, "PUBLICLINK_REVOKE", "public_link", f"{link.link_id} {link.token_hint}",
          before=before | {"token_hash": "***"}, after=row_snapshot(link) | {"token_hash": "***"})
    return link


def enable(session: Session, actor: str, settings: AppSettings, ref: str, is_public: Any = KEEP) -> PublicLink:
    """Switches a revoked link on again: its old address works again. Checked again as a whole (its
    report, its user) - an expiry passed meanwhile keeps it closed until ``change`` moves it.
    ``is_public`` set in the same step: a link coming back for signed-in readers is never public
    in between."""
    link = find(session, ref)
    if is_public is not KEEP:
        link.is_public = bool(is_public)
    before = row_snapshot(link)
    _checked(session, settings, report_code=link.report.report_code, run_as=link.run_as.username,
             params=link.params_json, editable=link.editable_json, view=link.view_key,
             refresh_seconds=link.refresh_seconds, expires_at=link.expires_at,
             choice_list=link.choice_list.list_code if link.choice_list else None, new_expiry=False)
    link.is_active = True
    audit(session, actor, "PUBLICLINK_ENABLE", "public_link", f"{link.link_id} {link.token_hint}",
          before=before | {"token_hash": "***"}, after=row_snapshot(link) | {"token_hash": "***"})
    return link


def resolve(session: Session, token: str) -> PublicLink | None:
    """The usable link of a token, or None - for every reason alike (unknown, revoked, expired, its
    report or its user switched off): a reader learns nothing about other links."""
    if not token or len(token) > 100:
        return None
    link = session.scalars(select(PublicLink).where(PublicLink.token_hash == token_hash(token))).first()
    if link is None or not link.is_active:
        return None
    if link.expires_at is not None and link.expires_at <= utcnow():
        return None
    if not link.report.is_active or not link.run_as.is_active:
        return None
    from drscore.authz.permissions import require_report

    try:  # its user's grant withdrawn: closed too, and not a page that says so under the report's name
        require_report(session, link.run_as, link.report.report_code)
    except DRSError:
        return None
    return link


def params_for(link: PublicLink, query: dict[str, Any]) -> dict[str, Any]:
    """The link's values, and the reader's for the parameters it lets them change."""
    values = dict(link.params_json or {})
    for name in link.editable_json or []:
        if name in query and query[name] not in (None, ""):
            values[name] = query[name]
    return values


def touch(session: Session, link_id: int) -> None:
    session.execute(update(PublicLink).where(PublicLink.link_id == link_id)
                    .values(last_used_at=utcnow(), use_count=PublicLink.use_count + 1)
                    .execution_options(synchronize_session=False))


def list_all(session: Session) -> list[PublicLink]:
    return list(session.scalars(select(PublicLink).order_by(PublicLink.link_id)))


def describe(link: PublicLink) -> dict[str, Any]:
    return {"id": link.link_id, "token": link.token_hint + "...", "report": link.report.report_code,
            "run_as": link.run_as.username, "view": link.view_key, "title": link.title, "params": link.params_json,
            "editable": link.editable_json, "refresh_seconds": link.refresh_seconds, "expires_at": link.expires_at,
            "choice_list": link.choice_list.list_code if link.choice_list else None,
            "public": link.is_public, "frame": link.frame_ancestors, "active": link.is_active, "created_by": link.created_by, "created_at": link.created_at,
            "last_used_at": link.last_used_at, "use_count": link.use_count}
