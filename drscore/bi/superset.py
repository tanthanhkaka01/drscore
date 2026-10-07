"""Calls from the DRS server to Apache Superset (spec sections 14.3, 14.4).

DRS signs in with the service account of ``[bi.superset]`` (it may only read dashboards and
issue guest tokens), checks that an embedded dashboard exists, and asks for guest tokens limited
to one dashboard. The browser never sees the service account.

Row-level security in the guest token (spec 14.4 step 5) is not built yet (owner, 2026-10-06):
``guest_token`` always sends an empty ``rls`` list, and a report with row-filter rules is refused
in embedded mode by ``drscore.render.design``.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import closing
from typing import Any, Protocol

import httpx

from drscore.errors import DRSError
from drscore.settings import SupersetSection

CHECK_CACHE_SECONDS = 60


class SupersetApi(Protocol):
    def embedded_exists(self, dashboard_uuid: str) -> bool: ...

    def guest_token(self, dashboard_uuid: str, user: dict[str, str], rls: list[dict[str, Any]]) -> str: ...


class SupersetClient:
    """The real client, over Superset's REST API (httpx)."""

    def __init__(self, cfg: SupersetSection, timeout: float = 10.0):
        self.cfg = cfg
        self.timeout = timeout

    def _password(self) -> str:
        value = os.environ.get(self.cfg.password_env)
        if not value:
            raise DRSError("BI_ENGINE_UNAVAILABLE",
                           admin_detail=f"the Superset service password is not set ({self.cfg.password_env})")
        return value

    def _signed_in(self) -> tuple[httpx.Client, dict[str, str]]:
        client = httpx.Client(base_url=self.cfg.api_url.rstrip("/"), timeout=self.timeout)
        try:
            r = client.post("/api/v1/security/login", json={
                "username": self.cfg.username, "password": self._password(), "provider": "db", "refresh": False})
            if r.status_code != 200:
                raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset login: HTTP {r.status_code}")
            headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
            return client, headers
        except httpx.HTTPError as exc:
            client.close()
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset at {self.cfg.api_url}: {exc}") from exc
        except DRSError:
            client.close()
            raise

    def embedded_exists(self, dashboard_uuid: str) -> bool:
        client, headers = self._signed_in()
        with closing(client):  # already open: the sign-in request used it
            try:
                r = client.get(f"/api/v1/embedded_dashboard/{dashboard_uuid}", headers=headers)
            except httpx.HTTPError as exc:
                raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=str(exc)) from exc
        if r.status_code == 404:
            return False
        if r.status_code != 200:
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset embedded_dashboard: HTTP {r.status_code}")
        return True

    def guest_token(self, dashboard_uuid: str, user: dict[str, str], rls: list[dict[str, Any]]) -> str:
        client, headers = self._signed_in()
        with closing(client):  # already open: the sign-in request used it
            try:
                csrf = client.get("/api/v1/security/csrf_token/", headers=headers)
                if csrf.status_code == 200:
                    headers = {**headers, "X-CSRFToken": csrf.json()["result"], "Referer": self.cfg.api_url}
                r = client.post("/api/v1/security/guest_token/", headers=headers, json={
                    "user": user, "resources": [{"type": "dashboard", "id": dashboard_uuid}], "rls": rls})
            except httpx.HTTPError as exc:
                raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=str(exc)) from exc
        if r.status_code != 200:
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset guest_token: HTTP {r.status_code}")
        return r.json()["token"]


_override: SupersetApi | None = None
_checks: dict[str, tuple[float, bool]] = {}
_checks_lock = threading.Lock()


def client(cfg: SupersetSection) -> SupersetApi:
    return _override or SupersetClient(cfg)


def set_client(api: SupersetApi | None) -> None:
    """Replaces the Superset client (tests use a stub); None restores the real one."""
    global _override
    _override = api
    with _checks_lock:
        _checks.clear()


def dashboard_exists(cfg: SupersetSection, dashboard_uuid: str) -> bool:
    """Asks Superset whether the embedded dashboard exists; the answer is kept 60 seconds."""
    now = time.monotonic()
    with _checks_lock:
        hit = _checks.get(dashboard_uuid)
        if hit and now - hit[0] < CHECK_CACHE_SECONDS:
            return hit[1]
    exists = client(cfg).embedded_exists(dashboard_uuid)
    with _checks_lock:
        _checks[dashboard_uuid] = (now, exists)
    return exists
