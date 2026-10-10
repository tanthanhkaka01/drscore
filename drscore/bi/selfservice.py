"""Self-service Superset integration driven by DRS (spec section 4).

Manages Superset datasets, roles, permissions and dashboard embedding directly
via Superset's REST API. Available for PostgreSQL DRS databases only; on SQLite
all operations return "not available on SQLite" and perform no actions.
"""

from __future__ import annotations

import logging
import os
import threading
import urllib.parse
from typing import Any, Iterable, Protocol

import httpx
from sqlalchemy import select

from drscore.audit import audit
from drscore.authz.design import can_edit_report, design_rights, param_datasource_ids
from drscore.bi import dataset as bi_dataset
from drscore.bi.superset import clear_dashboard_cache
from drscore.db.engine import Database
from drscore.db.models import BiDataset, Report, ReportGroup, ReportRowFilter, User
from drscore.errors import DRSError
from drscore.settings import AppSettings, Settings, SupersetSection

log = logging.getLogger(__name__)

INLINE = False

# Superset's role permission list is read, changed and written back whole: two syncs of the same
# role at once would lose one change. One sync at a time in this process.
_sync_lock = threading.Lock()


def _is_sqlite(database: Database) -> bool:
    """True if physical_name returns no schema (SQLite mode)."""
    schema, _ = bi_dataset.physical_name(database, "check_dummy")
    return schema is None


def _app(settings: AppSettings | Settings) -> AppSettings:
    return settings.app if isinstance(settings, Settings) else settings


def _superset_cfg(settings: AppSettings | Settings) -> SupersetSection:
    return _app(settings).bi.superset


def _base_url(settings: AppSettings | Settings) -> str:
    return _app(settings).app.base_url


def is_on(settings: AppSettings | Settings) -> bool:
    """Self-service is on: Superset is enabled, ``self_service`` is true and an admin account is named."""
    cfg = _superset_cfg(settings)
    return bool(cfg.enabled and cfg.self_service and cfg.admin_username)


# --------------------------------------------------------------------------------------------
# 4.1 Admin Client Protocol and Implementation
# --------------------------------------------------------------------------------------------

class SupersetAdmin(Protocol):
    def database_id(self, name: str) -> int: ...

    def find_dataset(self, database_id: int, schema: str, table: str) -> dict[str, Any] | None: ...

    def create_dataset(self, database_id: int, schema: str, table: str) -> dict[str, Any]: ...

    def refresh_dataset(self, dataset_id: int) -> None: ...

    def role_id(self, name: str, create: bool = False) -> int | None: ...

    def roles_with_prefix(self, prefix: str) -> dict[str, int]: ...

    def datasource_access_id(self, perm: str) -> int: ...

    def role_permission_ids(self, role_id: int) -> set[int]: ...

    def set_role_permission_ids(self, role_id: int, ids: Iterable[int]) -> None: ...

    def dashboards_of_dataset(self, dataset_id: int) -> list[dict[str, Any]]: ...

    def dashboard_dataset_ids(self, dashboard_id: int) -> set[int]: ...

    def enable_embedding(self, dashboard_id: int, allowed_domains: list[str]) -> str: ...

    def embedded_dashboard_id(self, uuid: str) -> int | None: ...


class SupersetAdminClient:
    """Client for Superset 6.x REST API using the admin account."""

    def __init__(self, cfg: SupersetSection, timeout: float = 10.0):
        self.cfg = cfg
        self.timeout = timeout
        self._token: str | None = None
        self._csrf: str | None = None
        # One HTTP client for the whole conversation: Superset ties the CSRF token to the session
        # cookie it sets, so a POST from another client is refused ("CSRF session token is missing").
        self._http: httpx.Client | None = None

    def _password(self) -> str:
        val = os.environ.get(self.cfg.admin_password_env)
        if not val:
            raise DRSError("BI_ENGINE_UNAVAILABLE",
                           admin_detail=f"the Superset admin password is not set ({self.cfg.admin_password_env})")
        return val

    def _login(self, client: httpx.Client) -> str:
        password = self._password()
        try:
            r = client.post("/api/v1/security/login", json={
                "username": self.cfg.admin_username,
                "password": password,
                "provider": "db",
                "refresh": False,
            })
            if r.status_code != 200:
                raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset login: HTTP {r.status_code}")
            return r.json()["access_token"]
        except httpx.HTTPError as exc:
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset at {self.cfg.api_url}: {exc}") from exc

    def _auth_headers(self, client: httpx.Client, is_write: bool = False) -> dict[str, str]:
        if not self._token:
            self._token = self._login(client)
        headers = {"Authorization": f"Bearer {self._token}"}
        if is_write:
            if not self._csrf:
                try:
                    r = client.get("/api/v1/security/csrf_token/", headers=headers)
                    if r.status_code == 200:
                        self._csrf = r.json().get("result")
                except httpx.HTTPError as exc:
                    raise DRSError("BI_ENGINE_UNAVAILABLE",
                                   admin_detail=f"Superset csrf_token at {self.cfg.api_url}: {exc}") from exc
            if self._csrf:
                headers["X-CSRFToken"] = self._csrf
            headers["Referer"] = self.cfg.api_url
        return headers

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        is_write = method.upper() in ("POST", "PUT", "PATCH", "DELETE")
        if self._http is None:
            self._http = httpx.Client(base_url=self.cfg.api_url.rstrip("/"), timeout=self.timeout)
        c = self._http
        headers = self._auth_headers(c, is_write=is_write)
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))
        try:
            return c.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise DRSError("BI_ENGINE_UNAVAILABLE",
                           admin_detail=f"Superset {method} {path} at {self.cfg.api_url}: {exc}") from exc

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def database_id(self, name: str) -> int:
        q = f"(filters:!((col:database_name,opr:eq,value:'{name}')))"
        r = self._request("GET", f"/api/v1/database/?q={urllib.parse.quote(q)}")
        if r.status_code != 200:
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset database query: HTTP {r.status_code}")
        results = r.json().get("result", [])
        if not results:
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset database {name!r} not found")
        return results[0]["id"]

    def find_dataset(self, database_id: int, schema: str, table: str) -> dict[str, Any] | None:
        q = f"(filters:!((col:table_name,opr:eq,value:'{table}'),(col:schema,opr:eq,value:'{schema}'),(col:database,opr:rel_o_m,value:{database_id})))"
        r = self._request("GET", f"/api/v1/dataset/?q={urllib.parse.quote(q)}")
        if r.status_code != 200:
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset find_dataset: HTTP {r.status_code}")
        results = r.json().get("result", [])
        if not results:
            return None
        item = results[0]
        ds_id = item["id"]
        perm = item.get("perm")
        if not perm:
            detail_r = self._request("GET", f"/api/v1/dataset/{ds_id}")
            if detail_r.status_code == 200:
                perm = detail_r.json().get("result", {}).get("perm")
        if not perm:
            perm = f"[{self.cfg.database_name}].[{table}](id:{ds_id})"
        return {"id": ds_id, "perm": perm}

    def create_dataset(self, database_id: int, schema: str, table: str) -> dict[str, Any]:
        body = {"database": database_id, "schema": schema, "table_name": table}
        r = self._request("POST", "/api/v1/dataset/", json=body)
        if r.status_code not in (200, 201):
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset create_dataset: HTTP {r.status_code}")
        created_id = r.json().get("id") or r.json().get("result", {}).get("id")
        found = self.find_dataset(database_id, schema, table)
        if found:
            return found
        return {"id": created_id, "perm": f"[{self.cfg.database_name}].[{table}](id:{created_id})"}

    def refresh_dataset(self, dataset_id: int) -> None:
        r = self._request("PUT", f"/api/v1/dataset/{dataset_id}/refresh")
        if r.status_code not in (200, 201):
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset refresh_dataset: HTTP {r.status_code}")

    def role_id(self, name: str, create: bool = False) -> int | None:
        q = f"(filters:!((col:name,opr:eq,value:'{name}')))"
        r = self._request("GET", f"/api/v1/security/roles/?q={urllib.parse.quote(q)}")
        if r.status_code != 200:
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset role query: HTTP {r.status_code}")
        results = r.json().get("result", [])
        if results:
            return results[0]["id"]
        if create:
            r = self._request("POST", "/api/v1/security/roles/", json={"name": name})
            if r.status_code not in (200, 201):
                raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset create role: HTTP {r.status_code}")
            return r.json().get("id") or r.json().get("result", {}).get("id")
        return None

    def roles_with_prefix(self, prefix: str) -> dict[str, int]:
        page = 0
        page_size = 100
        roles: dict[str, int] = {}
        while True:
            q = f"(filters:!((col:name,opr:sw,value:'{prefix}')),page:{page},page_size:{page_size})"
            r = self._request("GET", f"/api/v1/security/roles/?q={urllib.parse.quote(q)}")
            if r.status_code != 200:
                raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset roles query: HTTP {r.status_code}")
            data = r.json()
            items = data.get("result", [])
            for it in items:
                roles[it["name"]] = it["id"]
            count = data.get("count", 0)
            if len(roles) >= count or not items:
                break
            page += 1
        return roles

    def datasource_access_id(self, perm: str) -> int:
        # 1. find permission id for datasource_access
        q_perm = "(filters:!((col:name,opr:eq,value:datasource_access)))"
        r = self._request("GET", f"/api/v1/security/permissions/?q={urllib.parse.quote(q_perm)}")
        if r.status_code != 200 or not r.json().get("result"):
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail="Superset datasource_access permission not found")
        pid = r.json()["result"][0]["id"]

        # 2. find resource id for perm
        q_res = f"(filters:!((col:name,opr:eq,value:'{perm}')))"
        r = self._request("GET", f"/api/v1/security/resources/?q={urllib.parse.quote(q_res)}")
        if r.status_code != 200 or not r.json().get("result"):
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset resource {perm!r} not found")
        vid = r.json()["result"][0]["id"]

        # 3. find permission-resource id
        q_pv = f"(filters:!((col:permission,opr:rel_o_m,value:{pid}),(col:view_menu,opr:rel_o_m,value:{vid})))"
        r = self._request("GET", f"/api/v1/security/permissions-resources/?q={urllib.parse.quote(q_pv)}")
        if r.status_code != 200 or not r.json().get("result"):
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset permission-resource for {perm!r} not found")
        return r.json()["result"][0]["id"]

    def role_permission_ids(self, role_id: int) -> set[int]:
        r = self._request("GET", f"/api/v1/security/roles/{role_id}/permissions/")
        if r.status_code != 200:
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset role permissions: HTTP {r.status_code}")
        items = r.json().get("result", [])
        return {it["id"] for it in items}

    def set_role_permission_ids(self, role_id: int, ids: Iterable[int]) -> None:
        body = {"permission_view_menu_ids": sorted(list(ids))}
        r = self._request("POST", f"/api/v1/security/roles/{role_id}/permissions", json=body)
        if r.status_code not in (200, 201):
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset set role permissions: HTTP {r.status_code}")

    def dashboards_of_dataset(self, dataset_id: int) -> list[dict[str, Any]]:
        r = self._request("GET", f"/api/v1/dataset/{dataset_id}/related_objects")
        if r.status_code != 200:
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset related_objects: HTTP {r.status_code}")
        # Superset 6.1 answers {"charts": {...}, "dashboards": {"count", "result": [...]}} at the top
        # level, without the "result" wrapper of its other endpoints (checked on a live server).
        data = r.json()
        data = data.get("result", data) if "dashboards" not in data else data
        dashboards_raw = data.get("dashboards", {}).get("result", []) if isinstance(data.get("dashboards"), dict) else []
        return [
            {
                "id": d["id"],
                "title": d.get("title") or d.get("dashboard_title", ""),
                "slug": d.get("slug"),
            }
            for d in dashboards_raw
        ]

    def dashboard_dataset_ids(self, dashboard_id: int) -> set[int]:
        r = self._request("GET", f"/api/v1/dashboard/{dashboard_id}/datasets")
        if r.status_code != 200:
            raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset dashboard datasets: HTTP {r.status_code}")
        items = r.json().get("result", [])
        return {it["id"] for it in items}

    def enable_embedding(self, dashboard_id: int, allowed_domains: list[str]) -> str:
        r = self._request("GET", f"/api/v1/dashboard/{dashboard_id}/embedded")
        if r.status_code == 200:
            res = r.json().get("result", {})
            current_domains = set(res.get("allowed_domains", []))
            if current_domains == set(allowed_domains):
                return res["uuid"]
            # Domains differ, re-post
            r2 = self._request("POST", f"/api/v1/dashboard/{dashboard_id}/embedded", json={"allowed_domains": allowed_domains})
            if r2.status_code not in (200, 201):
                raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset update embedded: HTTP {r2.status_code}")
            return r2.json().get("result", {}).get("uuid", res["uuid"])
        if r.status_code == 404:
            r2 = self._request("POST", f"/api/v1/dashboard/{dashboard_id}/embedded", json={"allowed_domains": allowed_domains})
            if r2.status_code not in (200, 201):
                raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset create embedded: HTTP {r2.status_code}")
            return r2.json().get("result", {})["uuid"]
        raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset embedded query: HTTP {r.status_code}")

    def embedded_dashboard_id(self, uuid: str) -> int | None:
        r = self._request("GET", f"/api/v1/embedded_dashboard/{uuid}")
        if r.status_code == 200:
            res = r.json().get("result", {})
            return res.get("dashboard_id")
        if r.status_code == 404:
            return None
        raise DRSError("BI_ENGINE_UNAVAILABLE", admin_detail=f"Superset embedded_dashboard: HTTP {r.status_code}")


_admin_override: SupersetAdmin | None = None


def set_admin_client(stub: SupersetAdmin | None) -> None:
    global _admin_override
    _admin_override = stub


def admin_client(cfg: SupersetSection) -> SupersetAdmin:
    if _admin_override is not None:
        return _admin_override
    return SupersetAdminClient(cfg)


# --------------------------------------------------------------------------------------------
# 4.2 Operations
# --------------------------------------------------------------------------------------------

def prepare_dataset(database: Database, settings: AppSettings | Settings, report_code: str, actor: str) -> None:
    """Prepares the BI dataset of a report using its default parameters in the background."""
    settings = _app(settings)
    if _is_sqlite(database):
        return
    from drscore.reports.service import get_result, prepare

    try:
        with database.session() as s:
            report = s.scalars(select(Report).where(Report.report_code == report_code)).one_or_none()
            if report is None:
                log.info("prepare_dataset: report %s not found", report_code)
                return
            table = report.bi_dataset_table
            if not table:
                log.info("prepare_dataset: report %s has no bi_dataset_table", report_code)
                return
            report_id = report.report_id
            try:
                prep = prepare(s, report, {}, settings)
            except DRSError as exc:
                if exc.code == "PARAM_INVALID":
                    log.info("prepare_dataset: report %s has required parameter without default", report_code)
                    return
                raise
        outcome = get_result(database, prep, settings, username=actor)
        bi_dataset.refresh(database, report_id, table, outcome)
        log.info("prepare_dataset: prepared dataset %s for report %s", table, report_code)
    except Exception as exc:
        log.warning("prepare_dataset failed for report %s: %s", report_code, exc, exc_info=True)


def sync_report(database: Database, settings: AppSettings | Settings, report_id: int) -> str:
    """Syncs a single report's dataset and Superset role permissions."""
    settings = _app(settings)
    if _is_sqlite(database):
        return "not available on SQLite"

    with database.session() as s:
        report = s.get(Report, report_id)
        if report is None:
            return f"report {report_id} not found"
        if not report.is_active:
            return f"report {report.report_code} is inactive"
        if not report.bi_dataset_table:
            return f"report {report.report_code} has no BI dataset table"

        row = s.get(BiDataset, report_id)
        if row is None:
            return "dataset not made yet - run the report once"

        schema, table = bi_dataset.physical_name(database, report.bi_dataset_table)
        report_code = report.report_code
        group_code = report.group.group_code
        is_restricted = report.is_restricted

    cfg = _superset_cfg(settings)
    client = admin_client(cfg)
    with _sync_lock:
        return _sync_with_superset(database, client, cfg, report_id, report_code, group_code, is_restricted,
                                   schema, table)


def _sync_with_superset(database: Database, client: SupersetAdmin, cfg: SupersetSection, report_id: int,
                        report_code: str, group_code: str, is_restricted: bool, schema: str, table: str) -> str:
    with database.session() as s:
        db_id = client.database_id(cfg.database_name)
        ds = client.find_dataset(db_id, schema, table)
        if ds is None:
            ds = client.create_dataset(db_id, schema, table)
        dataset_id = ds["id"]
        perm = ds["perm"]
        client.refresh_dataset(dataset_id)

        perm_id = client.datasource_access_id(perm)
        prefix = cfg.design_role_prefix
        target_role_name = f"{prefix}{group_code}"
        roles_map = client.roles_with_prefix(prefix)

        removed_from = []
        if is_restricted:
            for rname, rid in roles_map.items():
                perms = client.role_permission_ids(rid)
                if perm_id in perms:
                    client.set_role_permission_ids(rid, perms - {perm_id})
                    removed_from.append(rname)
            outcome = f"synced {report_code} (dataset {table}, restricted: removed from roles)"
            audit_after = {"action": "sync", "restricted": True, "dataset_id": dataset_id, "removed_from": removed_from}
        else:
            if target_role_name not in roles_map:
                target_rid = client.role_id(target_role_name, create=True)
                roles_map[target_role_name] = target_rid
            else:
                target_rid = roles_map[target_role_name]

            target_perms = client.role_permission_ids(target_rid)
            if perm_id not in target_perms:
                client.set_role_permission_ids(target_rid, target_perms | {perm_id})

            for rname, rid in roles_map.items():
                if rname != target_role_name:
                    perms = client.role_permission_ids(rid)
                    if perm_id in perms:
                        client.set_role_permission_ids(rid, perms - {perm_id})
                        removed_from.append(rname)
            outcome = f"synced {report_code} (dataset {table} -> role {target_role_name})"
            audit_after = {"action": "sync", "dataset_id": dataset_id, "role": target_role_name, "removed_from": removed_from}

        audit(s, "drs", "BI_SYNC", "drs_report", report_code, after=audit_after)
        return outcome


def sync_group(database: Database, settings: AppSettings | Settings, group_code: str) -> list[str]:
    """Syncs a group's role and all its reports with Superset."""
    settings = _app(settings)
    if _is_sqlite(database):
        return ["not available on SQLite"]

    with database.session() as s:
        group = s.scalars(select(ReportGroup).where(ReportGroup.group_code == group_code)).one_or_none()
        if group is None:
            return [f"group {group_code} not found"]

        cfg = _superset_cfg(settings)
        client = admin_client(cfg)
        role_name = f"{cfg.design_role_prefix}{group_code}"
        client.role_id(role_name, create=True)

        reports = list(s.scalars(select(Report).where(Report.group_id == group.group_id, Report.is_active.is_(True))))
        outcomes = [f"role {role_name} ready"]
        for r in reports:
            outcomes.append(sync_report(database, settings, r.report_id))
        return outcomes


def schedule_sync(report_id: int) -> None:
    """Schedules sync_report in a daemon thread, or inline if INLINE=True."""
    if INLINE:
        from drscore.db import get_database
        from drscore.settings import get_settings
        database = get_database()
        settings = get_settings()
        sync_report(database, settings, report_id)
        return

    def _worker():
        try:
            from drscore.db import get_database
            from drscore.settings import get_settings
            database = get_database()
            settings = get_settings()
            sync_report(database, settings, report_id)
        except Exception as exc:
            log.warning("schedule_sync error for report %s: %s", report_id, exc, exc_info=True)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def report_dashboards(database: Database, settings: AppSettings | Settings, report_code: str) -> dict[str, Any]:
    """Returns dataset info and dashboards for the given report."""
    settings = _app(settings)
    if _is_sqlite(database):
        return {
            "dataset_id": None,
            "explore_url": "",
            "role": "",
            "dashboards": [],
            "linked_uuid": None,
            "error": "not available on SQLite",
        }

    with database.session() as s:
        report = s.scalars(select(Report).where(Report.report_code == report_code)).one_or_none()
        if report is None:
            raise DRSError("REPORT_NOT_FOUND")

        cfg = _superset_cfg(settings)
        role = f"{cfg.design_role_prefix}{report.group.group_code}"
        linked_uuid = None
        if report.bi_design_uri and report.bi_design_uri.startswith("superset:"):
            linked_uuid = report.bi_design_uri.split(":", 1)[1]

        if not report.bi_dataset_table:
            return {"dataset_id": None, "explore_url": "", "role": role, "dashboards": [], "linked_uuid": linked_uuid}

        row = s.get(BiDataset, report.report_id)
        if row is None:
            return {"dataset_id": None, "explore_url": "", "role": role, "dashboards": [], "linked_uuid": linked_uuid}

        schema, table = bi_dataset.physical_name(database, report.bi_dataset_table)
        client = admin_client(cfg)
        db_id = client.database_id(cfg.database_name)
        ds = client.find_dataset(db_id, schema, table)
        if ds is None:
            return {"dataset_id": None, "explore_url": "", "role": role, "dashboards": [], "linked_uuid": linked_uuid}

        dataset_id = ds["id"]
        explore_url = f"{cfg.base_url}/explore/?datasource_type=table&datasource_id={dataset_id}"
        dash_items = client.dashboards_of_dataset(dataset_id)
        dashboards = []
        for d in dash_items:
            did = d["id"]
            title = d.get("title") or d.get("dashboard_title") or f"Dashboard {did}"
            ds_ids = client.dashboard_dataset_ids(did)
            if ds_ids == {dataset_id}:
                usable = True
                reason = None
            elif dataset_id not in ds_ids:
                usable = False
                reason = "Dashboard does not use this dataset"
            else:
                usable = False
                reason = f"Dashboard uses {len(ds_ids - {dataset_id})} other dataset(s)"
            dashboards.append({
                "id": did,
                "title": title,
                "usable": usable,
                "reason": reason,
                "url": f"{cfg.base_url}/superset/dashboard/{did}/",
            })

        return {
            "dataset_id": dataset_id,
            "explore_url": explore_url,
            "role": role,
            "dashboards": dashboards,
            "linked_uuid": linked_uuid,
        }


def link_dashboard(
    database: Database,
    settings: AppSettings | Settings,
    user: User,
    report_code: str,
    dashboard_id: int,
) -> str:
    """Attaches a Superset dashboard to a report and enables its embedding."""
    settings = _app(settings)
    if _is_sqlite(database):
        return "not available on SQLite"

    with database.session() as s:
        report = s.scalars(select(Report).where(Report.report_code == report_code)).one_or_none()
        if report is None:
            raise DRSError("REPORT_NOT_FOUND")

        rights = design_rights(s, user)
        if not can_edit_report(rights, report, param_datasource_ids(s, report.report_id)):
            raise DRSError("DESIGN_FORBIDDEN")
        if report.group_id not in rights.publish_group_ids:
            raise DRSError("DESIGN_FORBIDDEN")

        row_filters = list(s.scalars(select(ReportRowFilter).where(ReportRowFilter.report_id == report.report_id)))
        if row_filters:
            # Embedded mode refuses a report with row filters (the guest token carries no row rule).
            raise DRSError("DRAFT_INVALID", detail={"reason": "row_filters"})

        if not report.bi_dataset_table or s.get(BiDataset, report.report_id) is None:
            raise DRSError("DRAFT_INVALID", detail={"reason": "no_dataset"})

        cfg = _superset_cfg(settings)
        client = admin_client(cfg)
        schema, table = bi_dataset.physical_name(database, report.bi_dataset_table)
        db_id = client.database_id(cfg.database_name)
        ds = client.find_dataset(db_id, schema, table)
        if ds is None:
            raise DRSError("DRAFT_INVALID", detail={"reason": "no_dataset"})

        dataset_id = ds["id"]
        ds_ids = client.dashboard_dataset_ids(dashboard_id)
        if ds_ids != {dataset_id}:
            raise DRSError("DRAFT_INVALID", detail={"reason": "other_datasets"})

        base_url = _base_url(settings)
        parts = urllib.parse.urlsplit(base_url)
        origin = f"{parts.scheme}://{parts.netloc}"
        uuid = client.enable_embedding(dashboard_id, [origin])

        before_uri = report.bi_design_uri
        report.bi_design_uri = f"superset:{uuid}"
        report.updated_by = user.username
        s.flush()

        audit(s, user.username, "BI_LINK", "drs_report", report.report_code,
              before={"bi_design_uri": before_uri}, after={"bi_design_uri": report.bi_design_uri})
        clear_dashboard_cache(uuid)
        return uuid


def unlink_dashboard(
    database: Database,
    settings: AppSettings | Settings,
    user: User,
    report_code: str,
) -> str:
    """Detaches a Superset dashboard from a report."""
    settings = _app(settings)
    if _is_sqlite(database):
        return "not available on SQLite"

    with database.session() as s:
        report = s.scalars(select(Report).where(Report.report_code == report_code)).one_or_none()
        if report is None:
            raise DRSError("REPORT_NOT_FOUND")

        rights = design_rights(s, user)
        if not can_edit_report(rights, report, param_datasource_ids(s, report.report_id)):
            raise DRSError("DESIGN_FORBIDDEN")
        if report.group_id not in rights.publish_group_ids:
            raise DRSError("DESIGN_FORBIDDEN")

        before_uri = report.bi_design_uri
        report.bi_design_uri = None
        report.updated_by = user.username
        s.flush()

        audit(s, user.username, "BI_UNLINK", "drs_report", report.report_code,
              before={"bi_design_uri": before_uri}, after={"bi_design_uri": None})
        return "unlinked"


def superset_urls(
    database: Database,
    settings: AppSettings | Settings,
    report_code: str,
) -> dict[str, str | None]:
    """Returns Superset explore and dashboard URLs for a report."""
    settings = _app(settings)
    if _is_sqlite(database) or not is_on(settings):
        return {"explore": None, "dashboard": None}

    with database.session() as s:
        report = s.scalars(select(Report).where(Report.report_code == report_code)).one_or_none()
        if report is None:
            raise DRSError("REPORT_NOT_FOUND")

        bi_table = report.bi_dataset_table
        bi_uri = report.bi_design_uri
        rep_id = report.report_id
        has_bi_dataset = s.get(BiDataset, rep_id) is not None

    cfg = _superset_cfg(settings)
    client = admin_client(cfg)
    explore_url = None
    dashboard_url = None

    if bi_table and has_bi_dataset:
        schema, table = bi_dataset.physical_name(database, bi_table)
        if schema is not None:
            db_id = client.database_id(cfg.database_name)
            ds = client.find_dataset(db_id, schema, table)
            if ds is not None:
                explore_url = f"{cfg.base_url}/explore/?datasource_type=table&datasource_id={ds['id']}"

    if bi_uri and bi_uri.startswith("superset:"):
        uuid = bi_uri.split(":", 1)[1]
        dash_id = client.embedded_dashboard_id(uuid)
        if dash_id is not None:
            dashboard_url = f"{cfg.base_url}/superset/dashboard/{dash_id}/"

    return {"explore": explore_url, "dashboard": dashboard_url}
