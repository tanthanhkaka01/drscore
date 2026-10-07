"""CLI commands of the report engine: datasource, report, cache, seed-demo (spec section 18)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from sqlalchemy import select

from drs.errors import DRSError


def _ctx():
    from drs.db import get_database
    from drs.settings import get_settings

    return get_database(), get_settings().app


def _fail(message: str):
    from drs.cli import CommandError

    raise CommandError(message)


# --------------------------------------------------------------------------------------------
# datasource

def cmd_datasource_add(args: argparse.Namespace) -> int:
    from drs.audit import audit, cli_actor
    from drs.cli import _read_password
    from drs.datasources import secrets
    from pydantic import ValidationError

    from drs.datasources.spec import DatasourceSpec
    from drs.db.models import Datasource

    options: dict[str, str] = {}
    for item in args.option or []:
        key, sep, value = item.partition("=")
        if not sep:
            _fail(f"--option {item!r}: use KEY=VALUE")
        options[key] = value
    try:
        spec = DatasourceSpec(
            code=args.code, name=args.name, description=args.description, db_type=args.type, host=args.host,
            port=args.port, instance_name=args.instance, database=args.database,
            oracle_connect_by="sid" if args.sid else None, default_schema=args.schema,
            auth_method=args.auth or ("none" if args.no_password else "password"), username=args.user,
            ssl_mode=args.ssl_mode, trust_server_certificate=args.trust_server_certificate,
            connect_timeout_seconds=args.connect_timeout, driver=args.driver, options=options)
    except ValidationError as exc:
        _fail("; ".join(f"{'.'.join(map(str, e['loc'])) or 'datasource'}: {e['msg']}" for e in exc.errors()))
    if spec.auth_method != "password":
        secret_ref = None
    elif args.password_env:
        secret_ref = f"env:{args.password_env}"
    else:
        secret_ref = secrets.encrypt(_read_password(args))  # fails clearly when the key is missing
    database, _ = _ctx()
    with database.session() as s:
        if s.scalars(select(Datasource).where(Datasource.datasource_code == spec.code)).first():
            _fail(f"Datasource {spec.code} already exists.")
        s.add(Datasource(
            datasource_code=spec.code, datasource_name=spec.name or spec.code, description=spec.description,
            db_type=spec.db_type, host=spec.host, port=spec.port, instance_name=spec.instance_name,
            database_name=spec.database, oracle_connect_by=spec.oracle_connect_by, default_schema=spec.default_schema,
            auth_method=spec.auth_method, username=spec.username, secret_ref=secret_ref, ssl_mode=spec.ssl_mode,
            trust_server_certificate=spec.trust_server_certificate,
            connect_timeout_seconds=spec.connect_timeout_seconds, driver=spec.driver,
            options_json=spec.options or None))
        audit(s, cli_actor(), "DATASOURCE_ADD", "datasource", spec.code,
              after={**spec.model_dump(exclude={"options"}), "secret": (secret_ref or "").split(":")[0] or None})
    print(f"Datasource {spec.code} added"
          f"{' (password encrypted)' if secret_ref and secret_ref.startswith('enc:') else ''}.")
    return 0


def cmd_datasource_show(args: argparse.Namespace) -> int:
    from drs.db.models import Datasource

    database, _ = _ctx()
    with database.session() as s:
        ds = s.scalars(select(Datasource).where(Datasource.datasource_code == args.code)).one_or_none()
        if ds is None:
            _fail(f"No datasource {args.code}.")
        secret = (ds.secret_ref or "").split(":")[0]
        rows = [
            ("Code", ds.datasource_code), ("Name", ds.datasource_name), ("Description", ds.description),
            ("Type", ds.db_type), ("Host", ds.host), ("Port", ds.port), ("Instance", ds.instance_name),
            ("Database", ds.database_name), ("Oracle connect by", ds.oracle_connect_by),
            ("Default schema", ds.default_schema), ("Authentication", ds.auth_method), ("User", ds.username),
            ("Password", {"enc": "stored encrypted", "env": f"from {ds.secret_ref[4:]}" if ds.secret_ref else ""}
             .get(secret, "-")),
            ("SSL mode", ds.ssl_mode or "driver default"), ("Trust server certificate", ds.trust_server_certificate),
            ("Connect timeout (s)", ds.connect_timeout_seconds or "15 (default)"), ("Driver", ds.driver),
            ("Other options", ds.options_json), ("Active", ds.is_active), ("Updated", ds.updated_at),
        ]
        for label, value in rows:
            print(f"{label:<26} {'' if value is None else value}")
    return 0


def cmd_datasource_set_password(args: argparse.Namespace) -> int:
    from drs.audit import audit, cli_actor
    from drs.cli import _read_password
    from drs.datasources import secrets
    from drs.db.models import Datasource

    database, _ = _ctx()
    with database.session() as s:
        ds = s.scalars(select(Datasource).where(Datasource.datasource_code == args.code)).one_or_none()
        if ds is None:
            _fail(f"No datasource {args.code}.")
        ds.secret_ref = f"env:{args.password_env}" if args.password_env else secrets.encrypt(_read_password(args))
        ds.auth_method = "password"
        audit(s, cli_actor(), "DATASOURCE_SET_PASSWORD", "datasource", ds.datasource_code,
              after={"secret": ds.secret_ref.split(":")[0]})
    print(f"Password of {args.code} stored ({'environment variable' if args.password_env else 'encrypted'}).")
    return 0


def cmd_datasource_list(args: argparse.Namespace) -> int:
    from drs.db.models import Datasource

    database, _ = _ctx()
    with database.session() as s:
        rows = list(s.scalars(select(Datasource).order_by(Datasource.datasource_code)))
        print(f"{'CODE':<16} {'TYPE':<11} {'ACTIVE':<7} {'SECRET':<7} TARGET")
        for d in rows:
            target = d.database_name if d.db_type == "sqlite" else f"{d.username or ''}@{d.host}:{d.port or ''}/{d.database_name}"
            secret = (d.secret_ref or "-").split(":")[0]
            print(f"{d.datasource_code:<16} {d.db_type:<11} {'yes' if d.is_active else 'no':<7} {secret:<7} {target}")
        print(f"{len(rows)} datasource(s).")
    return 0


def cmd_datasource_test(args: argparse.Namespace) -> int:
    from drs.datasources.connectors import SourceInfo
    from drs.db.models import Datasource
    from drs.reports import executor

    database, settings = _ctx()
    with database.session() as s:
        ds = s.scalars(select(Datasource).where(Datasource.datasource_code == args.code)).one_or_none()
        if ds is None:
            _fail(f"No datasource {args.code}.")
        src = SourceInfo.of(ds)
    try:
        result = executor.run_query(src, executor.ping_sql(src.db_type), (), {}, timeout_seconds=30, max_rows=1,
                                    max_bytes=1024, tz=settings.tz)
    except DRSError as exc:
        _fail(f"{exc.code}: {exc.admin_detail or exc.message}")
    print(f"Datasource {args.code}: connected, test query answered in {result.duration_ms} ms.")
    return 0


# --------------------------------------------------------------------------------------------
# report

def _parse_params(items: list[str] | None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep:
            _fail(f"--param {item!r}: use NAME=VALUE")
        if key in params:  # repeated: a multiselect
            params[key] = (params[key] if isinstance(params[key], list) else [params[key]]) + [value]
        else:
            params[key] = value
    return params


def cmd_report_run(args: argparse.Namespace) -> int:
    from drs.reports.service import run_for_user

    if args.format != "grid" and args.format != "json":
        _fail(f"--format {args.format}: XLSX / CSV / TXT export comes in milestone 4.")
    database, settings = _ctx()
    try:
        served = run_for_user(database, settings, args.as_user.lower(), args.code, _parse_params(args.param),
                              refresh=args.refresh, view=args.view)
    except DRSError as exc:
        detail = exc.detail if exc.code == "PARAM_INVALID" else (exc.admin_detail or exc.message)
        _fail(f"{exc.code}: {detail}")
    o = served.outcome
    meta = {"snapshot_id": o.snapshot_id, "created_at": o.created_at.isoformat(),
            "expires_at": o.expires_at.isoformat(), "cache_hit": o.cache_hit, "is_stale": o.is_stale,
            "params": o.params, "row_count": len(served.rows)}
    if args.format == "json":
        text = json.dumps({"report": served.report, "snapshot": meta, "columns": o.columns, "rows": served.rows},
                          ensure_ascii=False, indent=1)
    else:
        text = _grid_text(o.columns, served.rows, args.limit) + "\n" + json.dumps(meta, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"{len(served.rows)} row(s) written to {args.out}.")
    else:
        print(text)
    return 0


def _grid_text(columns, rows, limit: int) -> str:
    names = [c["name"] for c in columns]
    shown = [["" if v is None else str(v) for v in r] for r in rows[:limit]]
    widths = [min(40, max([len(n)] + [len(r[i]) for r in shown])) for i, n in enumerate(names)]
    lines = [" | ".join(n.ljust(w)[:w] for n, w in zip(names, widths)),
             "-+-".join("-" * w for w in widths)]
    lines += [" | ".join(v.ljust(w)[:w] for v, w in zip(r, widths)) for r in shown]
    if len(rows) > limit:
        lines.append(f"... {len(rows) - limit} more row(s)")
    return "\n".join(lines)


def cmd_report_validate(args: argparse.Namespace) -> int:
    from drs.reports.validate import validate_reports

    database, settings = _ctx()
    with database.session() as s:
        results = validate_reports(s, settings, args.code)
    errors = 0
    for code, findings in results:
        worst = "ERROR" if any(f[0] == "ERROR" for f in findings) else (
            "WARN" if any(f[0] == "WARN" for f in findings) else "OK")
        errors += worst == "ERROR"
        print(f"{worst:<5} {code}")
        for level, message in findings:
            print(f"      {level:<5} {message}")
    if args.code and not results:
        _fail(f"No report {args.code}.")
    print(f"{len(results)} report(s) checked, {errors} with errors.")
    return 1 if errors else 0


# --------------------------------------------------------------------------------------------
# cache

def _report_id(s, code: str) -> int:
    from drs.db.models import Report

    rid = s.scalars(select(Report.report_id).where(Report.report_code == code)).one_or_none()
    if rid is None:
        _fail(f"No report {code}.")
    return rid


def cmd_cache_purge(args: argparse.Namespace) -> int:
    from drs.reports import cache

    database, settings = _ctx()
    with database.session() as s:
        rid = _report_id(s, args.report) if args.report else None
        moved = cache.archive_expired(s, report_id=rid)
        locks = cache.purge_stale_locks(s, settings.cache.lock_stale_seconds)
    print(f"Moved {moved} expired snapshot(s) to the history; removed {locks} stale lock(s).")
    return 0


def cmd_cache_clear(args: argparse.Namespace) -> int:
    from drs.audit import audit, cli_actor
    from drs.reports import cache

    database, _ = _ctx()
    with database.session() as s:
        moved = cache.archive_report(s, _report_id(s, args.report))
        audit(s, cli_actor(), "CACHE_CLEAR", "report", args.report, after={"moved_to_history": moved})
    print(f"Moved {moved} snapshot(s) of {args.report} to the history.")
    return 0


def cmd_cache_warm(args: argparse.Namespace) -> int:
    from drs.db.models import Report, ReportGroup
    from drs.reports.service import get_result, prepare

    database, settings = _ctx()
    with database.session() as s:
        stmt = (select(Report).join(ReportGroup).where(Report.is_active.is_(True), ReportGroup.is_active.is_(True),
                                                       Report.query_text.is_not(None))
                .order_by(Report.report_code))
        if args.report:
            stmt = stmt.where(Report.report_code == args.report)
        reports = list(s.scalars(stmt))
    if args.report and not reports:
        _fail(f"No active report {args.report} with a query.")
    failed = 0
    for report in reports:
        try:
            with database.session() as s:
                prep = prepare(s, s.get(Report, report.report_id), {}, settings)
            outcome = get_result(database, prep, settings, username="cache-warm")
            state = "ran the source" if outcome.executed else "still valid"
            if report.bi_dataset_table:
                from drs.bi import dataset as bi_dataset

                if bi_dataset.refresh(database, report.report_id, report.bi_dataset_table, outcome):
                    state += f", dataset {bi_dataset.display_name(database, report.bi_dataset_table)} loaded"
            print(f"OK    {report.report_code}: {state}, {len(outcome.rows)} row(s), snapshot {outcome.snapshot_id}")
        except DRSError as exc:
            if exc.code == "PARAM_INVALID":
                print(f"SKIP  {report.report_code}: a required parameter has no default")
                continue
            failed += 1
            print(f"ERROR {report.report_code}: {exc.code}: {exc.admin_detail or exc.message}")
    return 1 if failed else 0


# --------------------------------------------------------------------------------------------
# seed-demo

def cmd_seed_demo(args: argparse.Namespace) -> int:
    from drs.demo.seed import SOURCE_PATH, SeedRefused, seed

    database, settings = _ctx()
    try:
        passwords = seed(database, settings, force=args.force)
    except SeedRefused as exc:
        _fail(str(exc))
    print(f"Demo source written to {SOURCE_PATH}; demo groups, reports, role and users created.")
    print("Passwords (shown once, not stored in clear):")
    for username, password in passwords.items():
        print(f"  {username:<8} {password}")
    from drs.demo.seed import USERS

    kept = [u for u in USERS if u not in passwords]
    if kept:
        print(f"Existing users kept with their password: {', '.join(kept)}")
    return 0


# --------------------------------------------------------------------------------------------
# Parsers

# --------------------------------------------------------------------------------------------
# metadata

def cmd_metadata_export(args: argparse.Namespace) -> int:
    from pathlib import Path

    from drs.admin import metadata

    database, _ = _ctx()
    try:
        with database.session() as s:
            data = metadata.export(s, reports=args.report, source=database.settings.describe())
    except metadata.MetadataError as exc:
        _fail(str(exc))
    Path(args.out).write_text(metadata.dumps(data), encoding="utf-8")
    print(f"Wrote {args.out}: {len(data['datasources'])} datasources, {len(data['groups'])} groups, "
          f"{len(data['reports'])} reports, {len(data['users'])} users, {len(data['roles'])} roles, "
          f"{len(data['group_grants']) + len(data['report_grants'])} grants. No passwords, no secrets.")
    return 0


def cmd_metadata_import(args: argparse.Namespace) -> int:
    from pathlib import Path

    from drs.admin import metadata
    from drs.audit import cli_actor

    database, _ = _ctx()
    try:
        data = metadata.parse(Path(args.in_).read_text(encoding="utf-8"))
    except OSError as exc:
        _fail(f"cannot read {args.in_}: {exc}")
    except metadata.MetadataError as exc:
        _fail(str(exc))
    with database.sessionmaker() as s:
        try:
            result = metadata.import_metadata(s, data, cli_actor())
        except metadata.MetadataError as exc:
            s.rollback()
            _fail(f"{exc} - nothing was imported")
        if args.dry_run:
            s.rollback()
        else:
            s.commit()
    for label, items in (("Created", result.created), ("Updated", result.updated), ("Removed", result.removed)):
        for item in items:
            print(f"  {label:8} {item}")
    for note in result.notes:
        print(f"  NOTE     {note}")
    verb = "Would import" if args.dry_run else "Imported"
    print(f"{verb}: {len(result.created)} created, {len(result.updated)} updated, {len(result.removed)} removed, "
          f"{result.unchanged} unchanged.{' Nothing was changed (--dry-run).' if args.dry_run else ''}")
    return 0


def add_parsers(sub) -> None:
    from drs.cli import _password_option

    ds = sub.add_parser("datasource", help="report sources").add_subparsers(dest="action", required=True)
    p = ds.add_parser("add", help="add a source; the password is asked and stored encrypted")
    p.add_argument("code")
    p.add_argument("--type", required=True, help="mssql, oracle, postgresql or sqlite")
    p.add_argument("--name")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--database", required=True,
                   help="database name; the service name for oracle; the file path for sqlite")
    p.add_argument("--user")
    p.add_argument("--driver", help="mssql: pymssql or an ODBC driver name; oracle: thick")
    p.add_argument("--description")
    p.add_argument("--instance", help="SQL Server named instance, e.g. SQLEXPRESS")
    p.add_argument("--sid", action="store_true", help="oracle: --database is a SID, not a service name")
    p.add_argument("--schema", help="default schema (PostgreSQL search_path, Oracle CURRENT_SCHEMA)")
    p.add_argument("--auth", choices=["password", "windows", "none"], help="authentication (default password)")
    p.add_argument("--ssl-mode", choices=["disable", "prefer", "require", "verify"])
    p.add_argument("--trust-server-certificate", action="store_true")
    p.add_argument("--connect-timeout", type=int, help="seconds (default 15)")
    p.add_argument("--option", action="append", help="any other driver option KEY=VALUE (repeatable)")
    p.add_argument("--password-env", help="read the password from this environment variable at run time")
    p.add_argument("--no-password", action="store_true")
    _password_option(p)
    p.set_defaults(handler=cmd_datasource_add)
    ds.add_parser("list", help="list sources").set_defaults(handler=cmd_datasource_list)
    p = ds.add_parser("show", help="every connection setting of a source (never the password)")
    p.add_argument("code")
    p.set_defaults(handler=cmd_datasource_show)
    p = ds.add_parser("set-password", help="store a new password (hidden prompt), encrypted")
    p.add_argument("code")
    p.add_argument("--password-env", help="use this environment variable instead")
    _password_option(p)
    p.set_defaults(handler=cmd_datasource_set_password)
    p = ds.add_parser("test", help="connect and run a trivial query")
    p.add_argument("code")
    p.set_defaults(handler=cmd_datasource_test)

    rep = sub.add_parser("report", help="reports").add_subparsers(dest="action", required=True)
    p = rep.add_parser("run", help="run a report exactly as a user would get it")
    p.add_argument("--code", required=True)
    p.add_argument("--param", action="append", help="NAME=VALUE (repeat a name for a multiselect)")
    p.add_argument("--as-user", required=True)
    p.add_argument("--format", default="grid", help="grid | json (xlsx | csv | txt: milestone 4)")
    p.add_argument("--out")
    p.add_argument("--refresh", action="store_true", help="bypass the cache (needs can_refresh)")
    p.add_argument("--view", default="grid", choices=["grid", "html"],
                   help="the view whose checks apply (default grid)")
    p.add_argument("--limit", type=int, default=50, help="rows printed in grid format")
    p.set_defaults(handler=cmd_report_run)
    p = rep.add_parser("validate", help="check report definitions")
    p.add_argument("--code")
    p.set_defaults(handler=cmd_report_validate)

    c = sub.add_parser("cache", help="JSON report cache").add_subparsers(dest="action", required=True)
    p = c.add_parser("purge", help="move every expired snapshot to the history; remove stale locks")
    p.add_argument("--report")
    p.set_defaults(handler=cmd_cache_purge)
    p = c.add_parser("clear", help="move every snapshot of one report to the history")
    p.add_argument("--report", required=True)
    p.set_defaults(handler=cmd_cache_clear)
    p = c.add_parser("warm", help="run reports with their default parameters ahead of the users")
    p.add_argument("--report")
    p.set_defaults(handler=cmd_cache_warm)

    m = sub.add_parser("metadata", help="report definitions as one JSON file").add_subparsers(
        dest="action", required=True)
    p = m.add_parser("export", help="write groups, reports, datasources (no secrets), users (no passwords), "
                                    "roles and grants to a JSON file")
    p.add_argument("--out", required=True)
    p.add_argument("--report", action="append", help="only this report (repeatable), with what it needs")
    p.set_defaults(handler=cmd_metadata_export)
    p = m.add_parser("import", help="create / update from an exported file, matched by code")
    p.add_argument("--in", dest="in_", required=True, metavar="FILE")
    p.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    p.set_defaults(handler=cmd_metadata_import)

    p = sub.add_parser("seed-demo", help="demo source, reports and users")
    p.add_argument("--force", action="store_true", help="replace the demo objects")
    p.set_defaults(handler=cmd_seed_demo)
