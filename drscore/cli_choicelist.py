"""CLI commands of the choice lists: choices ticked on a page and copied for the reports
(docs/choice-lists.md)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _ctx():
    from drscore.db import get_database
    from drscore.settings import get_settings

    return get_database(), get_settings().app


def _fail(message: str):
    from drscore.cli import CommandError

    raise CommandError(message)


def _publish(database, settings, code: str) -> int:
    from drscore import choicelist

    try:
        return choicelist.publish(database, settings, code)
    except Exception as exc:  # the list is saved; only its copy failed - say so, and how to redo it
        _fail(f"list {code} saved, but its table could not be written: {exc} "
              f"(`drscore choicelist publish {code}` writes it again)")


def cmd_choicelist_import(args: argparse.Namespace) -> int:
    from drscore import choicelist
    from drscore.audit import cli_actor

    try:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail(f"{args.file}: {exc}")
    specs = data.get("lists") if isinstance(data, dict) and "lists" in data else [data]
    if not isinstance(specs, list) or not all(isinstance(x, dict) for x in specs):
        _fail(f"{args.file}: a list {{code, title, table, items: [...]}}, or {{\"lists\": [...]}}")
    database, settings = _ctx()
    done = []
    try:
        with database.session() as s:  # all or nothing
            for spec in specs:
                lst, counts = choicelist.put(s, cli_actor(), spec, reset=args.reset)
                done.append((lst.list_code, lst.target_table, counts))
    except choicelist.ListError as exc:
        _fail(f"{exc} (nothing was imported)")
    for code, table, counts in done:
        rows = _publish(database, settings, code)
        print(f"List {code}: {counts['added']} item(s) added, {counts['changed']} changed, "
              f"{counts['removed']} removed; table {table}: {rows} row(s).")
    return 0


def cmd_choicelist_export(args: argparse.Namespace) -> int:
    from drscore import choicelist

    database, _ = _ctx()
    with database.session() as s:
        if args.code:
            try:
                doc = choicelist.export_list(choicelist.find(s, args.code, active=False))
            except choicelist.ListError as exc:
                _fail(str(exc))
        else:
            doc = {"lists": [choicelist.export_list(x) for x in choicelist.list_all(s)]}
    text = json.dumps(doc, ensure_ascii=False, indent=2)
    if args.file:
        Path(args.file).write_text(text + "\n", encoding="utf-8")
        print(f"Written to {args.file}.")
    else:
        print(text)
    return 0


def cmd_choicelist_list(args: argparse.Namespace) -> int:
    from drscore import choicelist

    database, settings = _ctx()
    with database.session() as s:
        lists = choicelist.list_all(s)
        print(f"{'CODE':<28} {'TABLE':<28} {'ITEMS':>5} {'TICKED':>6} {'ACTIVE':<7} LAST CHANGE")
        for x in lists:
            when = x.changed_at.astimezone(settings.tz).strftime("%Y-%m-%d %H:%M") if x.changed_at else "-"
            print(f"{x.list_code:<28} {x.target_table:<28} {len(x.items):>5} "
                  f"{sum(1 for i in x.items if i.is_checked):>6} {'yes' if x.is_active else 'no':<7} "
                  f"{when} {x.changed_by or ''}")
        print(f"{len(lists)} list(s).")
    return 0


def cmd_choicelist_show(args: argparse.Namespace) -> int:
    from drscore import choicelist

    database, settings = _ctx()
    with database.session() as s:
        try:
            x = choicelist.find(s, args.code, active=False)
        except choicelist.ListError as exc:
            _fail(str(exc))
        print(f"{x.list_code} - {x.title} (table {x.target_table}{'' if x.is_active else ', switched off'})")
        for i in x.items:
            when = i.changed_at.astimezone(settings.tz).strftime("%Y-%m-%d %H:%M") if i.changed_at else ""
            print(f"  [{'x' if i.is_checked else ' '}] {i.item_key:<24} {i.label:<44} {i.group_name or '':<24} "
                  f"{when} {i.changed_by or ''}")
    return 0


def _keys(text: str | None) -> list[str]:
    return [k.strip() for k in (text or "").split(",") if k.strip()]


def cmd_choicelist_tick(args: argparse.Namespace) -> int:
    from drscore import choicelist
    from drscore.audit import cli_actor

    on, off = _keys(args.on), _keys(args.off)
    if not on and not off:
        _fail("--on and/or --off: the item keys to tick and untick")
    if set(on) & set(off):
        _fail("an item in --on and --off: " + ", ".join(sorted(set(on) & set(off))))
    database, settings = _ctx()
    try:
        with database.session() as s:
            changed = choicelist.tick(s, cli_actor(), args.code, {**{k: True for k in on}, **{k: False for k in off}})
    except choicelist.ListError as exc:
        _fail(str(exc))
    rows = _publish(database, settings, args.code)
    print(f"List {args.code}: {len(changed)} item(s) changed ({', '.join(changed) or 'none'}); table: {rows} row(s).")
    return 0


def cmd_choicelist_publish(args: argparse.Namespace) -> int:
    database, settings = _ctx()
    from drscore import choicelist

    with database.session() as s:
        try:
            choicelist.find(s, args.code, active=False)
        except choicelist.ListError as exc:
            _fail(str(exc))
    rows = _publish(database, settings, args.code)
    print(f"List {args.code}: table written, {rows} row(s).")
    return 0


def add_parsers(sub) -> None:
    cl = sub.add_parser("choicelist", help="choice lists: choices ticked on a page, copied for the reports"
                        ).add_subparsers(dest="action", required=True)
    p = cl.add_parser("import", help="add or change lists from a JSON file (an item keeps its tick)")
    p.add_argument("file")
    p.add_argument("--reset", action="store_true", help="also set every tick as the file says")
    p.set_defaults(handler=cmd_choicelist_import)
    p = cl.add_parser("export", help="a list (or all) as JSON, ticks included")
    p.add_argument("code", nargs="?")
    p.add_argument("--file", help="write it to this file")
    p.set_defaults(handler=cmd_choicelist_export)
    cl.add_parser("list", help="the lists").set_defaults(handler=cmd_choicelist_list)
    p = cl.add_parser("show", help="one list and its ticks")
    p.add_argument("code")
    p.set_defaults(handler=cmd_choicelist_show)
    p = cl.add_parser("tick", help="tick / untick items (the table is written again)")
    p.add_argument("code")
    p.add_argument("--on", help="comma-separated item keys to tick")
    p.add_argument("--off", help="comma-separated item keys to untick")
    p.set_defaults(handler=cmd_choicelist_tick)
    p = cl.add_parser("publish", help="write the list's table in the mirror schema again")
    p.add_argument("code")
    p.set_defaults(handler=cmd_choicelist_publish)
