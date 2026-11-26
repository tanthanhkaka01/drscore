"""CLI commands of the public links: a report opened by a secret URL (docs/public-links.md)."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timezone


def _ctx():
    from drscore.db import get_database
    from drscore.settings import get_settings

    return get_database(), get_settings().app


def _fail(message: str):
    from drscore.cli import CommandError

    raise CommandError(message)


def _expiry(value: str | None, tz) -> datetime | None:
    """A date (the end of that day, app timezone) or an ISO date-time."""
    if not value:
        return None
    try:
        if len(value) == 10:
            return datetime.combine(date.fromisoformat(value), time(23, 59, 59), tz).astimezone(timezone.utc)
        moment = datetime.fromisoformat(value)
        return (moment if moment.tzinfo else moment.replace(tzinfo=tz)).astimezone(timezone.utc)
    except ValueError:
        _fail(f"--expires {value!r}: YYYY-MM-DD or an ISO date-time")


def _params(items: list[str] | None) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            _fail(f"--param {item!r}: use NAME=VALUE")
        params[key] = value
    return params


def _names(text: str | None) -> list[str]:
    return [n.strip() for n in (text or "").split(",") if n.strip()]


def _yes_no(value: str) -> bool:
    if value.lower() in ("yes", "y", "1", "on", "true"):
        return True
    if value.lower() in ("no", "n", "0", "off", "false"):
        return False
    _fail(f"--public {value!r}: yes or no")


def _who(public: bool, run_as: str) -> str:
    return f"runs as {run_as}" if public else "sign-in required; runs as the reader, with their own rights"


def cmd_publiclink_add(args: argparse.Namespace) -> int:
    from drscore import publiclink
    from drscore.audit import cli_actor

    params = _params(args.param)
    editable = _names(args.editable)
    public = _yes_no(args.public)
    database, settings = _ctx()
    try:
        with database.session() as s:
            link, token = publiclink.create(
                s, cli_actor(), settings, report_code=args.report, run_as=args.run_as, params=params,
                editable=editable, view=args.view, title=args.title, refresh_seconds=args.refresh,
                expires_at=_expiry(args.expires, settings.tz), choice_list=args.choice_list, is_public=public,
                framers=args.frame)
            link_id = link.link_id
    except publiclink.LinkError as exc:
        _fail(str(exc))
    print(f"Link {link_id} made for {args.report} ({_who(public, args.run_as)}). Its address - shown this once:")
    print(publiclink.url_of(settings, token))
    from drscore import siteurl
    for other in siteurl.portal_origins(settings)[1:]:  # the same link at the portal's other addresses
        print(other + publiclink.PATH + token)
    if public and not settings.public.enabled:
        print("NOTE: [public] enabled = false in app.toml: the address answers 'not found' until it is true.")
    return 0


def cmd_publiclink_list(args: argparse.Namespace) -> int:
    from drscore import publiclink

    database, settings = _ctx()
    with database.session() as s:
        links = publiclink.list_all(s)
        print(f"{'ID':>4} {'TOKEN':<11} {'REPORT':<24} {'RUN AS':<20} {'VIEW':<5} {'PUBLIC':<7} {'ACTIVE':<7} "
              f"{'EXPIRES':<17} {'USES':>6} LAST USED")
        for link in links:
            exp = link.expires_at.astimezone(settings.tz).strftime("%Y-%m-%d %H:%M") if link.expires_at else "-"
            used = link.last_used_at.astimezone(settings.tz).strftime("%Y-%m-%d %H:%M") if link.last_used_at else "-"
            print(f"{link.link_id:>4} {link.token_hint + '...':<11} {link.report.report_code:<24} "
                  f"{link.run_as.username if link.is_public else '(the reader)':<20} {link.view_key:<5} "
                  f"{'yes' if link.is_public else 'no':<7} {'yes' if link.is_active else 'no':<7} {exp:<17} "
                  f"{link.use_count:>6} {used}")
        print(f"{len(links)} link(s); public links are {'on' if settings.public.enabled else 'off'} ([public] enabled).")
    return 0


def cmd_publiclink_show(args: argparse.Namespace) -> int:
    from drscore import publiclink

    database, _ = _ctx()
    with database.session() as s:
        try:
            info = publiclink.describe(publiclink.find(s, args.ref))
        except publiclink.LinkError as exc:
            _fail(str(exc))
    print(json.dumps(info, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_publiclink_set(args: argparse.Namespace) -> int:
    """Changes what a link shows; its address stays the same. Options not given are kept."""
    from drscore import publiclink
    from drscore.audit import cli_actor

    keep = publiclink.KEEP
    if args.param and args.no_params:
        _fail("--param and --no-params together")
    changes = {
        "report_code": args.report or keep,
        "run_as": args.run_as or keep,
        "params": None if args.no_params else (_params(args.param) if args.param else keep),
        "editable": keep if args.editable is None else (_names(args.editable) or None),
        "view": args.view or keep,
        "title": keep if args.title is None else (args.title or None),
        "refresh_seconds": keep if args.refresh is None else (args.refresh or None),
        "choice_list": keep if args.choice_list is None else (args.choice_list or None),
        "is_public": keep if args.public is None else _yes_no(args.public),
        "framers": keep if args.frame is None else (args.frame or None),
    }
    database, settings = _ctx()
    if args.expires is not None:
        changes["expires_at"] = _expiry(args.expires, settings.tz) if args.expires else None
    try:
        with database.session() as s:
            link = publiclink.change(s, cli_actor(), settings, args.ref, **changes)
            info = publiclink.describe(link)
    except publiclink.LinkError as exc:
        _fail(str(exc))
    print(f"Link {info['id']} ({info['token']}) now shows {info['report']} ({info['view']}), "
          f"{_who(info['public'], info['run_as'])}"
          + (f", and may tick list {info['choice_list']}" if info["choice_list"] else "")
          + "; its address is unchanged.")
    return 0


def cmd_publiclink_enable(args: argparse.Namespace) -> int:
    from drscore import publiclink
    from drscore.audit import cli_actor

    database, settings = _ctx()
    try:
        with database.session() as s:
            info = publiclink.describe(publiclink.enable(
                s, cli_actor(), settings, args.ref,
                is_public=publiclink.KEEP if args.public is None else _yes_no(args.public)))
    except publiclink.LinkError as exc:
        _fail(str(exc))
    print(f"Link {info['id']} ({info['token']}) switched on again: its address works again, "
          f"{_who(info['public'], info['run_as'])}.")
    return 0


def cmd_publiclink_revoke(args: argparse.Namespace) -> int:
    from drscore import publiclink
    from drscore.audit import cli_actor

    database, _ = _ctx()
    try:
        with database.session() as s:
            link = publiclink.revoke(s, cli_actor(), args.ref)
            link_id, hint = link.link_id, link.token_hint
    except publiclink.LinkError as exc:
        _fail(str(exc))
    print(f"Public link {link_id} ({hint}...) switched off.")
    return 0


def add_parsers(sub) -> None:
    pl = sub.add_parser("publiclink", help="public links: a report opened without signing in").add_subparsers(
        dest="action", required=True)
    p = pl.add_parser("add", help="make a link; its address is printed once")
    p.add_argument("report", help="report code")
    p.add_argument("--as", dest="run_as", required=True,
                   help="the DRS user whose rights the report runs with (a viewer account, not an admin)")
    p.add_argument("--param", action="append", help="a parameter value NAME=VALUE (repeatable)")
    p.add_argument("--editable", help="comma-separated parameters the reader may change on the page")
    p.add_argument("--view", choices=["bi", "grid", "html"], default="bi")
    p.add_argument("--title", help="the page title (default: the report name)")
    p.add_argument("--refresh", type=int, help="seconds between reloads of the page (a wall screen), 30 or more")
    p.add_argument("--expires", help="YYYY-MM-DD (end of that day) or an ISO date-time; default: never")
    p.add_argument("--list", dest="choice_list",
                   help="a choice list the reader may tick on the page (view html; docs/choice-lists.md)")
    p.add_argument("--public", default="yes",
                   help="yes (default): anybody with the address, as --as sees it; no: sign-in required, "
                        "the report runs as the reader with their own rights")
    p.add_argument("--frame", help="the other sites whose pages may show the link in a frame: '*' for any, "
                                   "or sites such as https://hrms.example.com (default: this portal only)")
    p.set_defaults(handler=cmd_publiclink_add)
    pl.add_parser("list", help="the links and their use").set_defaults(handler=cmd_publiclink_list)
    p = pl.add_parser("show", help="one link (by id or the first characters of its token)")
    p.add_argument("ref")
    p.set_defaults(handler=cmd_publiclink_show)
    p = pl.add_parser("set", help="change what a link shows (by id or the first characters of its token); "
                                  "the address stays the same, options not given are kept")
    p.add_argument("ref")
    p.add_argument("--report", help="another report code")
    p.add_argument("--as", dest="run_as", help="another DRS user to run the report as")
    p.add_argument("--param", action="append", help="NAME=VALUE (repeatable): replaces all the link's values")
    p.add_argument("--no-params", action="store_true", help="the link fixes no parameter value")
    p.add_argument("--editable", help="comma-separated parameters the reader may change ('' for none)")
    p.add_argument("--view", choices=["bi", "grid", "html"])
    p.add_argument("--title", help="the page title ('' for the report name)")
    p.add_argument("--refresh", type=int, help="seconds between refreshes, 30 or more (0 for none)")
    p.add_argument("--expires", help="YYYY-MM-DD or an ISO date-time ('' for never)")
    p.add_argument("--list", dest="choice_list", help="a choice list the reader may tick ('' for none)")
    p.add_argument("--public", help="yes: anybody with the address; no: sign-in and the reader's own rights")
    p.add_argument("--frame", help="the sites that may show the link in a frame: '*', sites such as "
                                   "https://hrms.example.com, or '' for this portal only")
    p.set_defaults(handler=cmd_publiclink_set)
    p = pl.add_parser("revoke", help="switch a link off (by id or the first characters of its token)")
    p.add_argument("ref")
    p.set_defaults(handler=cmd_publiclink_revoke)
    p = pl.add_parser("enable", help="switch a revoked link on again: its old address works again")
    p.add_argument("ref")
    p.add_argument("--public", help="yes / no at the same time (default: as it was)")
    p.set_defaults(handler=cmd_publiclink_enable)
