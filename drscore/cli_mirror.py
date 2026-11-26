"""CLI commands of the mirrors: copies of source tables in the DRS database (docs/mirror.md)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from drscore.errors import DRSError


def _ctx():
    from drscore.db import get_database
    from drscore.settings import get_settings

    return get_database(), get_settings().app


def _fail(message: str):
    from drscore.cli import CommandError

    raise CommandError(message)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v))


def _spec_from_args(args: argparse.Namespace, base: dict[str, Any] | None = None) -> dict[str, Any]:
    from drscore.mirror.spec import as_columns

    data = dict(base or {})
    data["code"] = args.code
    for key, attr in (("datasource", "datasource"), ("source", "source"), ("target", "target"),
                      ("where", "where"), ("mode", "mode"), ("watermark", "watermark"),
                      ("lookback_seconds", "lookback"), ("interval_minutes", "interval"),
                      ("full_refresh_hours", "full_every"), ("run_hours", "hours"),
                      ("full_hours", "full_hours")):
        value = getattr(args, attr, None)
        if value is not None:
            data[key] = value if value != "" else None
    if getattr(args, "columns", None) is not None:
        data["columns"] = as_columns(args.columns)
    if getattr(args, "key", None) is not None:
        data["key"] = as_columns(args.key)
    if getattr(args, "read_uncommitted", None) is not None:
        data["read_uncommitted"] = args.read_uncommitted
    if getattr(args, "active", None) is not None:
        data["active"] = args.active
    return data


def _save(data: dict[str, Any]) -> tuple[str, bool]:
    from drscore.audit import cli_actor
    from drscore.mirror import service
    from drscore.mirror.spec import MirrorError, spec_of

    database, _ = _ctx()
    try:
        spec = spec_of(data)
        with database.session() as s:
            m, created = service.save(s, cli_actor(), spec)
            return m.mirror_code, created
    except MirrorError as exc:
        _fail(str(exc))


def cmd_mirror_add(args: argparse.Namespace) -> int:
    from drscore.mirror import service
    from drscore.mirror.spec import MirrorError

    database, _ = _ctx()
    with database.session() as s:
        try:
            service.get(s, args.code)
            _fail(f"Mirror {args.code} already exists: change it with `mirror set`.")
        except MirrorError:
            pass
    code, _ = _save(_spec_from_args(args))
    print(f"Mirror {code} added. It runs at its next due time, or now with `mirror run {code}`.")
    return 0


def cmd_mirror_set(args: argparse.Namespace) -> int:
    from drscore.mirror import service
    from drscore.mirror.spec import MirrorError

    database, _ = _ctx()
    with database.session() as s:
        try:
            base = service.spec_from_row(service.get(s, args.code))
        except MirrorError as exc:
            _fail(str(exc))
    code, _ = _save(_spec_from_args(args, base))
    if args.reset_watermark:
        from drscore.audit import cli_actor

        with database.session() as s:
            service.reset_watermark(s, cli_actor(), code)
    print(f"Mirror {code} changed.")
    return 0


def cmd_mirror_list(args: argparse.Namespace) -> int:
    from drscore.mirror import service

    database, settings = _ctx()
    with database.session() as s:
        rows = service.list_all(s)
        print(f"{'CODE':<36} {'SOURCE':<14} {'TARGET':<28} {'MODE':<5} {'EVERY':>6} {'STATUS':<8} "
              f"{'LAST RUN':<17} {'ROWS':>10} {'SECONDS':>8}")
        for m in rows:
            when = m.last_finished_at.astimezone(settings.tz).strftime("%Y-%m-%d %H:%M") if m.last_finished_at else "-"
            status = m.last_status or "-" if m.is_active else "off"
            secs = f"{m.last_duration_ms / 1000:.1f}" if m.last_duration_ms is not None else "-"
            print(f"{m.mirror_code:<36} {m.datasource.datasource_code:<14} {m.target_table:<28} "
                  f"{'full' if m.mode == 'full' else 'incr':<5} {str(m.interval_minutes) + 'm':>6} {status:<8} "
                  f"{when:<17} {m.last_rows if m.last_rows is not None else '-':>10} {secs:>8}")
        print(f"{len(rows)} mirror(s); schema {database.mirror_schema or database.settings.sqlite.mirror_path}; "
              f"background runs {'on' if settings.mirror.enabled else 'off'} ([mirror] enabled).")
    return 0


def cmd_mirror_show(args: argparse.Namespace) -> int:
    from drscore.mirror import service
    from drscore.mirror.spec import MirrorError

    database, _ = _ctx()
    with database.session() as s:
        try:
            m = service.get(s, args.code)
        except MirrorError as exc:
            _fail(str(exc))
        print(_json({**service.spec_from_row(m), "last_run": service.status_of(m)}))
    return 0


def cmd_mirror_run(args: argparse.Namespace) -> int:
    from drscore.mirror import runner, service

    database, settings = _ctx()
    if args.due:
        codes = runner.due(database, settings)
    elif args.all:
        with database.session() as s:
            codes = [m.mirror_code for m in service.list_all(s) if m.is_active]
    else:
        codes = args.codes
    if not codes:
        print("Nothing to run.")
        return 0
    failed = 0
    for code in codes:
        try:
            out = runner.sync(database, settings, code, force_full=args.full)
            print(f"{code}: {'full' if out.full else 'incremental'} copy, {out.rows} rows in {out.duration_ms / 1000:.1f} s"
                  + (f", watermark {out.watermark}" if out.watermark else ""))
        except Exception as exc:  # recorded on the mirror; the next one still runs
            failed += 1
            detail = exc.admin_detail or exc.message if isinstance(exc, DRSError) else str(exc)
            print(f"{code}: FAILED - {detail or type(exc).__name__}", file=sys.stderr)
    return 1 if failed else 0


def cmd_mirror_import(args: argparse.Namespace) -> int:
    from drscore.audit import cli_actor
    from drscore.mirror import service
    from drscore.mirror.spec import MirrorError, spec_of

    try:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail(f"{args.file}: {exc}")
    items = data.get("mirrors") if isinstance(data, dict) else data
    if not isinstance(items, list):
        _fail(f"{args.file}: a list of mirrors, or {{\"mirrors\": [...]}}")
    database, _ = _ctx()
    added = changed = 0
    try:
        with database.session() as s:  # all or nothing
            for item in items:
                _, created = service.save(s, cli_actor(), spec_of(item))
                added += created
                changed += not created
    except MirrorError as exc:
        _fail(f"{exc} (nothing was imported)")
    print(f"{added} mirror(s) added, {changed} changed.")
    return 0


def cmd_mirror_export(args: argparse.Namespace) -> int:
    from drscore.mirror import service

    database, _ = _ctx()
    with database.session() as s:
        doc = {"mirrors": [service.spec_from_row(m) for m in service.list_all(s)]}
    text = _json(doc)
    if args.file:
        Path(args.file).write_text(text + "\n", encoding="utf-8")
        print(f"{len(doc['mirrors'])} mirror(s) written to {args.file}.")
    else:
        print(text)
    return 0


def _definition_options(p: argparse.ArgumentParser, required: bool) -> None:
    p.add_argument("code", help="the mirror's name, e.g. ERP_A.CUSTINVOICETRANS")
    p.add_argument("--datasource", required=required, help="code of the source datasource")
    p.add_argument("--source", required=required, help="table or view to copy, [schema.]name")
    p.add_argument("--target", required=required,
                   help="table of the copy in the mirror schema (lower case); several sources may share one")
    p.add_argument("--columns", help="comma-separated source columns (default: every column)")
    p.add_argument("--where", help="a filter in the source's SQL, e.g. \"INVOICEDATE >= '2025-01-01'\"")
    p.add_argument("--key", help="comma-separated row key (required by incremental)")
    p.add_argument("--mode", choices=["full", "incremental"])
    p.add_argument("--watermark", help="incremental: a modification time or a growing id")
    p.add_argument("--lookback", type=int, help="incremental, time watermark: seconds read again (default 0)")
    p.add_argument("--interval", type=int, help="minutes between runs (default 60)")
    p.add_argument("--full-every", type=int, help="incremental: a full copy every N hours (removes deleted rows)")
    p.add_argument("--hours", help="hours it may start, app timezone, e.g. 0-6,22-23 (default any)")
    p.add_argument("--full-hours", help="incremental: hours the periodic full copy may start, e.g. 1-5")
    p.add_argument("--read-uncommitted", dest="read_uncommitted", action="store_true", default=None,
                   help="SQL Server: read without shared locks")
    p.add_argument("--no-read-uncommitted", dest="read_uncommitted", action="store_false")
    p.add_argument("--inactive", dest="active", action="store_false", default=None)
    p.add_argument("--active", dest="active", action="store_true")


def add_parsers(sub) -> None:
    mirror = sub.add_parser("mirror", help="copies of source tables in the DRS database").add_subparsers(
        dest="action", required=True)
    p = mirror.add_parser("add", help="copy a source table into the mirror schema")
    _definition_options(p, required=True)
    p.set_defaults(handler=cmd_mirror_add)
    p = mirror.add_parser("set", help="change a mirror (only the options given)")
    _definition_options(p, required=False)
    p.add_argument("--reset-watermark", action="store_true", help="the next run makes a full copy")
    p.set_defaults(handler=cmd_mirror_set)
    mirror.add_parser("list", help="mirrors and their last run").set_defaults(handler=cmd_mirror_list)
    p = mirror.add_parser("show", help="a mirror's definition and last run")
    p.add_argument("code")
    p.set_defaults(handler=cmd_mirror_show)
    p = mirror.add_parser("run", help="run mirrors now")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("codes", nargs="*", default=[])
    which.add_argument("--due", action="store_true", help="the mirrors that are due")
    which.add_argument("--all", action="store_true", help="every active mirror")
    p.add_argument("--full", action="store_true", help="a full copy, even of an incremental mirror")
    p.set_defaults(handler=cmd_mirror_run)
    p = mirror.add_parser("import", help="add or change mirrors from a JSON file")
    p.add_argument("file")
    p.set_defaults(handler=cmd_mirror_import)
    p = mirror.add_parser("export", help="the mirror definitions as JSON")
    p.add_argument("file", nargs="?")
    p.set_defaults(handler=cmd_mirror_export)
