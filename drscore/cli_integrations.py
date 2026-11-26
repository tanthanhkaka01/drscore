"""CLI commands of the integrations: encrypted secrets, Telegram / Zalo notices, the AI agent
(docs/feedback-typhon-notices.md)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _fail(message: str):
    from drscore.cli import CommandError

    raise CommandError(message)


def _value_from(args: argparse.Namespace) -> str:
    """The secret's value: a field of a JSON file, one line of stdin, or a hidden prompt.
    Never an argument, so it is not left in the shell history, and never printed."""
    if args.from_json:
        if not args.field:
            _fail("--from-json needs --field NAME (the field of the file that holds the value)")
        try:
            data = json.loads(Path(args.from_json).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            _fail(f"cannot read {args.from_json}: {exc.__class__.__name__}")
        value = data.get(args.field) if isinstance(data, dict) else None
        if not isinstance(value, str):
            _fail(f"{args.from_json} has no text field {args.field!r}")
    elif args.stdin:
        value = sys.stdin.readline().rstrip("\r\n").removeprefix("﻿")
    else:
        import getpass

        value = getpass.getpass("Value: ")
        if getpass.getpass("Repeat value: ") != value:
            _fail("The two values differ.")
    value = value.strip()
    if not value:
        _fail("Empty value.")
    return value


def cmd_secret_set(args: argparse.Namespace) -> int:
    from drscore import secretstore
    from drscore.audit import audit, cli_actor
    from drscore.db import get_database

    if args.name not in secretstore.KNOWN:
        _fail(f"unknown secret {args.name!r}: one of {', '.join(secretstore.KNOWN)}")
    value = _value_from(args)
    with get_database().session() as s:
        secretstore.put(s, args.name, value, cli_actor())  # fails clearly when DRS_SECRET_KEY is missing
        audit(s, cli_actor(), "SECRET_SET", "drs_secret", args.name)
    print(f"Secret {args.name} stored, encrypted ({len(value)} characters; the value is not shown).")
    return 0


def cmd_secret_list(args: argparse.Namespace) -> int:
    from drscore import secretstore
    from drscore.db import get_database

    with get_database().session() as s:
        rows = {r.secret_name: r for r in secretstore.listing(s)}
    for name, what in secretstore.KNOWN.items():
        row = rows.get(name)
        state = f"stored, encrypted (by {row.updated_by}, {row.updated_at:%Y-%m-%d %H:%M} UTC)" if row else "not stored"
        print(f"{name:<20} {state}  - {what}")
    return 0


def cmd_secret_remove(args: argparse.Namespace) -> int:
    from drscore import secretstore
    from drscore.audit import audit, cli_actor
    from drscore.db import get_database

    with get_database().session() as s:
        if not secretstore.remove(s, args.name):
            _fail(f"secret {args.name} is not stored")
        audit(s, cli_actor(), "SECRET_REMOVE", "drs_secret", args.name)
    print(f"Secret {args.name} removed.")
    return 0


def cmd_notify_status(args: argparse.Namespace) -> int:
    from drscore import notify
    from drscore.settings import get_settings

    settings = get_settings().app
    for channel in notify.channels(settings, only_ready=False):
        print(f"{channel.name:<9} {'ON' if channel.ready else 'off'}  {channel.why}")
    print(f"events: {', '.join(settings.notify.events) or '(none)'}")
    return 0


def cmd_notify_test(args: argparse.Namespace) -> int:
    """Sends one test notice, now, to every channel that is ready (or the one named)."""
    from drscore import notify
    from drscore.settings import get_settings

    settings = get_settings().app
    ready = [c for c in notify.channels(settings) if not args.channel or c.name == args.channel]
    if not ready:
        _fail("no channel is ready: set its chat_id in app.toml and its bot token (`notify status` says what is missing)")
    text = args.text or f"{settings.app.name}: test notice from `python -m drscore notify test`"
    failed = 0
    for channel in ready:
        ok, error = notify.send_now(settings, channel, "test", text)
        print(f"{channel.name}: {'sent' if ok else 'FAILED ' + (error or '')}")
        failed += 0 if ok else 1
    return 1 if failed else 0


def cmd_typhon_status(args: argparse.Namespace) -> int:
    from drscore.settings import get_settings
    from drscore.typhon import agent

    settings = get_settings().app
    state = agent.availability(settings)
    print(f"{settings.ai.name}: {'ON' if state.on else 'off'} - {state.why}")
    if state.on:
        print(f"model {settings.ai.model}, effort {settings.ai.effort}, report data "
              f"{'readable' if settings.ai.read_report_data else 'not readable'}, "
              f"budget {settings.ai.monthly_budget_usd} USD a month, spent this month "
              f"{agent.spent_this_month():.4f} USD")
    return 0


def add_parsers(sub, secret) -> None:
    from drscore import secretstore

    p = secret.add_parser("set", help="store an integration secret, encrypted with DRS_SECRET_KEY (never printed)")
    p.add_argument("name", choices=sorted(secretstore.KNOWN))
    src = p.add_mutually_exclusive_group()
    src.add_argument("--from-json", metavar="FILE", help="read the value from a field of this JSON file")
    src.add_argument("--stdin", action="store_true", help="read the value from one line of standard input")
    p.add_argument("--field", help="with --from-json: the field that holds the value")
    p.set_defaults(handler=cmd_secret_set)
    secret.add_parser("list", help="the integration secrets and whether each is stored (values never shown)"
                      ).set_defaults(handler=cmd_secret_list)
    p = secret.add_parser("remove", help="remove a stored integration secret")
    p.add_argument("name", choices=sorted(secretstore.KNOWN))
    p.set_defaults(handler=cmd_secret_remove)

    notify = sub.add_parser("notify", help="notices to a Telegram / Zalo group").add_subparsers(
        dest="action", required=True)
    notify.add_parser("status", help="which channel is ready, and what is missing").set_defaults(
        handler=cmd_notify_status)
    p = notify.add_parser("test", help="send one test notice now")
    p.add_argument("--channel", choices=["telegram", "zalo"])
    p.add_argument("--text")
    p.set_defaults(handler=cmd_notify_test)

    typhon = sub.add_parser("ai", help="the AI agent (Typhon)").add_subparsers(dest="action", required=True)
    typhon.add_parser("status", help="is the agent on, and why not").set_defaults(handler=cmd_typhon_status)
