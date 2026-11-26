"""What's new: /changes (the page) and /api/changelog (the "What's new" box), as each account may read them."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from drscore import changelog
from drscore.authz.design import design_rights
from drscore.db.models import User
from drscore.web.deps import current_user, db_session
from drscore.web.i18n import request_locale
from drscore.web.templating import render

router = APIRouter()


def _audience(session: Session, user: User) -> str:
    return changelog.audience_of(user.is_admin, design_rights(session, user).is_designer)


@router.get("/changes")
def changes_page(request: Request, user: User = Depends(current_user), session: Session = Depends(db_session)):
    releases = changelog.for_reader(request_locale(request), _audience(session, user))
    return render(request, "changes.html", releases=releases)


@router.get("/api/changelog")
def changes_api(request: Request, since: str | None = None, user: User = Depends(current_user),
                session: Session = Depends(db_session)) -> dict:
    releases = changelog.for_reader(request_locale(request), _audience(session, user), since=since or None,
                                    limit=changelog.SHOWN if since else 1)
    return {"version": changelog.version(),
            "releases": [{"version": r["version"], "date": r["date"],
                          "changes": [{"html": str(c["text"]), "details": [str(d) for d in c["details"]]}
                                      for c in r["changes"]]} for r in releases]}
