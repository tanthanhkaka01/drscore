"""``python -m drscore <command>`` (spec section 18).

Every command reads the same config files as the server, prints what it did, and exits non-zero
on failure.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Sequence

from drscore.errors import DRSError
from drscore.settings import ConfigError

Handler = Callable[[argparse.Namespace], int]


class CommandError(Exception):
    """A command could not do what was asked. The message is printed, the exit code is 1."""


# --------------------------------------------------------------------------------------------
# db

def cmd_db_upgrade(args: argparse.Namespace) -> int:
    from drscore.db import get_database
    from drscore.db.migrate import current_revision, upgrade

    database = get_database()
    print(f"Engine: {database.settings.describe()}")
    before = current_revision(database)
    upgrade(database)
    after = current_revision(database)
    if before == after:
        print(f"Already at revision {after}; nothing to do.")
    else:
        print(f"Upgraded from {before or '(empty database)'} to {after}.")
    return 0


def cmd_db_check(args: argparse.Namespace) -> int:
    from drscore.db import get_database
    from drscore.db.migrate import check

    result = check(get_database())
    print(f"Engine:   {result.engine}")
    print(f"Database: {result.target}")
    state = "up to date" if result.revision == result.head else f"behind - run 'db upgrade' (head {result.head})"
    print(f"Revision: {result.revision or '(none)'} ({state})")
    for table, status in result.tables.items():
        print(f"  {table:<26} {status}")
    print("OK" if result.ok else "PROBLEMS FOUND")
    return 0 if result.ok else 1


# --------------------------------------------------------------------------------------------
# secret

def cmd_secret_new_key(args: argparse.Namespace) -> int:
    from cryptography.fernet import Fernet

    print(Fernet.generate_key().decode("ascii"))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """A new installation in the DRS folder (DRS_HOME, or the current folder for a package installed
    with pip): config/ from the examples shipped inside the package, designs/ and runtime/. A file
    that exists is never touched."""
    from importlib import resources

    from drscore.settings import PROJECT_ROOT, config_dir

    config = config_dir()
    config.mkdir(parents=True, exist_ok=True)
    for name in ("app", "database"):
        target = config / f"{name}.toml"
        if target.exists():
            print(f"kept     {target}")
            continue
        example = resources.files("drscore").joinpath("examples", f"{name}.example.toml")
        target.write_bytes(example.read_bytes())
        print(f"created  {target}")
    for folder in ("designs", "runtime"):
        (PROJECT_ROOT / folder).mkdir(exist_ok=True)
    print(f"DRS folder: {PROJECT_ROOT}")
    print("Next: set the environment variable DRS_SECRET_KEY to the output of `python -m drscore secret "
          "new-key` (keep a copy of it), then `python -m drscore serve`. The database is SQLite under "
          "runtime/ until config/database.toml says otherwise; the first start creates the user "
          "admin / admin, who must change the password at the first sign-in.")
    return 0


# --------------------------------------------------------------------------------------------
# user / role / grant

def _admin_session():
    from drscore.db import get_database

    return get_database().session()


def _app_settings():
    from drscore.settings import get_settings

    return get_settings().app


def _read_password(args: argparse.Namespace) -> str:
    """A password from a hidden prompt (typed twice), or one line of stdin with --password-stdin.
    Never from an argument, so it does not end up in the shell history."""
    if getattr(args, "password_stdin", False):
        # Windows PowerShell puts a byte order mark in front of what it pipes: stored as part of
        # the password, it made the source refuse the login.
        password = sys.stdin.readline().rstrip("\r\n").removeprefix("﻿")
    else:
        import getpass

        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            raise CommandError("The two passwords differ.")
    if not password:
        raise CommandError("Empty password.")
    return password


def _admin_call(fn, *args, **kwargs):
    from drscore.admin.service import AdminError

    try:
        return fn(*args, **kwargs)
    except AdminError as exc:
        raise CommandError(str(exc)) from exc


def cmd_user_add(args: argparse.Namespace) -> int:
    from drscore.admin import service as admin
    from drscore.audit import cli_actor

    password = None if args.no_password else _read_password(args)
    with _admin_session() as s:
        user = _admin_call(admin.add_user, s, cli_actor(), _app_settings(), args.username,
                           args.display_name or args.username, password=password, email=args.email,
                           sso_subject=args.sso_subject, is_admin=args.admin)
        print(f"User {user.username} added{' (admin)' if user.is_admin else ''}"
              f"{'' if password else ' without a password'}.")
    return 0


def cmd_user_list(args: argparse.Namespace) -> int:
    from drscore.admin import service as admin

    with _admin_session() as s:
        users = admin.list_users(s)
        print(f"{'USERNAME':<24} {'DISPLAY NAME':<30} {'ADMIN':<6} {'ACTIVE':<7} {'PASSWORD':<9} SSO")
        for u in users:
            print(f"{u.username:<24} {u.display_name[:30]:<30} {'yes' if u.is_admin else 'no':<6} "
                  f"{'yes' if u.is_active else 'no':<7} {'set' if u.password_hash else '-':<9} "
                  f"{u.sso_subject or '-'}")
        print(f"{len(users)} user(s).")
    return 0


def cmd_user_active(args: argparse.Namespace) -> int:
    from drscore.admin import service as admin
    from drscore.audit import cli_actor

    active = args.action == "enable"
    with _admin_session() as s:
        user = _admin_call(admin.set_user_active, s, cli_actor(), args.username, active)
        print(f"User {user.username} {'enabled' if active else 'disabled; their sessions are ended'}.")
    return 0


def cmd_user_set_password(args: argparse.Namespace) -> int:
    from drscore.admin import service as admin
    from drscore.audit import cli_actor

    password = _read_password(args)
    with _admin_session() as s:
        user = _admin_call(admin.set_user_password, s, cli_actor(), _app_settings(), args.username, password)
        print(f"Password of {user.username} set; their sessions are ended.")
    return 0


def cmd_user_set_admin(args: argparse.Namespace) -> int:
    from drscore.admin import service as admin
    from drscore.audit import cli_actor

    with _admin_session() as s:
        user = _admin_call(admin.set_user_admin, s, cli_actor(), args.username, not args.off)
        print(f"User {user.username} is {'now' if user.is_admin else 'no longer'} an admin.")
    return 0


def cmd_user_attr(args: argparse.Namespace) -> int:
    from drscore.admin import service as admin
    from drscore.audit import cli_actor

    with _admin_session() as s:
        if args.attr_action == "set":
            values = _admin_call(admin.attr_set, s, cli_actor(), args.username, args.name, args.values)
            print(f"{args.username}.{args.name} = {', '.join(values)}")
        elif args.attr_action == "remove":
            count = _admin_call(admin.attr_remove, s, cli_actor(), args.username, args.name, args.value)
            print(f"Removed {count} value(s).")
        else:
            values = _admin_call(admin.attr_list, s, args.username)
            for name, vals in values.items():
                print(f"{name} = {', '.join(vals)}")
            if not values:
                print("No attributes.")
    return 0


def cmd_role(args: argparse.Namespace) -> int:
    from drscore.admin import service as admin
    from drscore.audit import cli_actor

    with _admin_session() as s:
        if args.action == "add":
            role = _admin_call(admin.add_role, s, cli_actor(), args.code, args.name)
            print(f"Role {role.role_code} added.")
        elif args.action == "list":
            for role, members in admin.list_roles(s):
                state = "" if role.is_active else " (inactive)"
                print(f"{role.role_code:<20} {role.role_name}{state}: {', '.join(members) or '-'}")
        else:
            add = args.member_action == "add"
            _admin_call(admin.role_member, s, cli_actor(), args.code, args.username, add)
            print(f"{args.username} {'added to' if add else 'removed from'} {args.code.upper()}.")
    return 0


def cmd_grant(args: argparse.Namespace) -> int:
    from drscore.admin import service as admin
    from drscore.audit import cli_actor

    with _admin_session() as s:
        g = _admin_call(admin.grant, s, cli_actor(), args.kind, args.code, args.user, args.role,
                        getattr(args, "export", None), getattr(args, "refresh", None),
                        getattr(args, "design", None))
        who = f"user {args.user}" if args.user else f"role {args.role}"
        if args.kind == "datasource":
            print(f"Granted datasource {args.code} to {who}.")
        elif args.kind == "group":
            print(f"Granted group {args.code} to {who} "
                  f"(export: {'yes' if g.can_export else 'no'}, refresh: {'yes' if g.can_refresh else 'no'}, "
                  f"design: {'yes' if g.can_design else 'no'}).")
        else:
            print(f"Granted report {args.code} to {who} "
                  f"(export: {'yes' if g.can_export else 'no'}, refresh: {'yes' if g.can_refresh else 'no'}).")
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    from drscore.admin import service as admin
    from drscore.audit import cli_actor

    with _admin_session() as s:
        _admin_call(admin.revoke, s, cli_actor(), args.kind, args.code, args.user, args.role)
        print(f"Revoked {args.kind} {args.code} from {'user ' + args.user if args.user else 'role ' + args.role}.")
    return 0


def cmd_grant_show(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from drscore.admin import service as admin
    from drscore.authz.design import design_rights
    from drscore.db.models import Datasource, ReportGroup

    with _admin_session() as s:
        rows = _admin_call(admin.grant_show, s, args.user)
        user = admin.get_user(s, args.user)
        print(f"{'GROUP':<12} {'REPORT':<24} {'TYPE':<5} {'EXPORT':<7} {'REFRESH':<8} VIA")
        for r in rows:
            print(f"{r['group']:<12} {r['report']:<24} {r['type']:<5} {'yes' if r['can_export'] else 'no':<7} "
                  f"{'yes' if r['can_refresh'] else 'no':<8} {'; '.join(r['via'])}")
        print(f"{len(rows)} report(s) visible to {args.user}.")

        rights = design_rights(s, user)
        if rights.group_ids:
            group_codes = sorted(s.scalars(select(ReportGroup.group_code).where(ReportGroup.group_id.in_(rights.group_ids))).all())
        else:
            group_codes = []
        if rights.datasource_ids:
            ds_codes = sorted(s.scalars(select(Datasource.datasource_code).where(Datasource.datasource_id.in_(rights.datasource_ids))).all())
        else:
            ds_codes = []
        print(f"Design groups: {', '.join(group_codes) or '-'}")
        print(f"Datasources: {', '.join(ds_codes) or '-'}")
    return 0


# --------------------------------------------------------------------------------------------
# serve

def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from drscore.db import get_database
    from drscore.settings import get_settings
    from drscore.startup import preflight

    settings = get_settings()
    database = get_database()
    print(f"DRS database: {database.settings.describe()}")
    for message in preflight(database, settings):
        print(message)
    database.dispose()  # the server processes open their own connections
    app_cfg = settings.app.app
    host = args.host or app_cfg.host
    port = args.port or app_cfg.port
    workers = args.workers or app_cfg.workers
    print(f"DRS listening on http://{host}:{port} ({workers} worker{'s' if workers > 1 else ''}); "
          f"users open {app_cfg.base_url}")
    uvicorn.run("drscore.app:create_app", factory=True, host=host, port=port, workers=workers,
                proxy_headers=True, forwarded_allow_ips=app_cfg.forwarded_allow_ips, server_header=False)
    return 0


# --------------------------------------------------------------------------------------------
# Parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m drscore", description="DRS - Dynamic Report System")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    sub.add_parser("init", help="a new installation here: config/, designs/, runtime/").set_defaults(
        handler=cmd_init)

    p = sub.add_parser("serve", help="start the web server")
    p.add_argument("--host", help="overrides [app] host")
    p.add_argument("--port", type=int, help="overrides [app] port")
    p.add_argument("--workers", type=int, help="overrides [app] workers")
    p.set_defaults(handler=cmd_serve)

    db = sub.add_parser("db", help="DRS database").add_subparsers(dest="action", required=True)
    db.add_parser("upgrade", help="create / migrate the DRS database").set_defaults(handler=cmd_db_upgrade)
    db.add_parser("check", help="engine, migration version, every table reachable").set_defaults(handler=cmd_db_check)

    secret = sub.add_parser("secret", help="encryption key").add_subparsers(dest="action", required=True)
    secret.add_parser("new-key", help="print a new encryption key").set_defaults(handler=cmd_secret_new_key)

    _add_user_parsers(sub)
    _add_role_parsers(sub)
    _add_grant_parsers(sub)

    from drscore.cli_reports import add_parsers

    add_parsers(sub)
    return parser


def _password_option(p: argparse.ArgumentParser) -> None:
    p.add_argument("--password-stdin", action="store_true",
                   help="read the password from one line of standard input instead of a prompt")


def _add_user_parsers(sub) -> None:
    user = sub.add_parser("user", help="users").add_subparsers(dest="action", required=True)

    p = user.add_parser("add", help="add a user (the password is asked at a hidden prompt)")
    p.add_argument("username")
    p.add_argument("--display-name")
    p.add_argument("--email")
    p.add_argument("--sso-subject", help="value of the SSO match claim for this user")
    p.add_argument("--admin", action="store_true")
    p.add_argument("--no-password", action="store_true", help="no password login (SSO only)")
    _password_option(p)
    p.set_defaults(handler=cmd_user_add)

    user.add_parser("list", help="list users").set_defaults(handler=cmd_user_list)
    for action in ("disable", "enable"):
        p = user.add_parser(action, help=f"{action} a user")
        p.add_argument("username")
        p.set_defaults(handler=cmd_user_active)

    p = user.add_parser("set-password", help="set a user's password (hidden prompt)")
    p.add_argument("username")
    _password_option(p)
    p.set_defaults(handler=cmd_user_set_password)

    p = user.add_parser("set-admin", help="make a user an admin (--off to remove)")
    p.add_argument("username")
    p.add_argument("--off", action="store_true")
    p.set_defaults(handler=cmd_user_set_admin)

    attr = user.add_parser("attr", help="user attributes for the row filter").add_subparsers(
        dest="attr_action", required=True)
    p = attr.add_parser("set", help="replace the values of one attribute ('*' = every value)")
    p.add_argument("username")
    p.add_argument("name")
    p.add_argument("values", nargs="+")
    p.set_defaults(handler=cmd_user_attr)
    p = attr.add_parser("remove", help="remove an attribute, or one of its values")
    p.add_argument("username")
    p.add_argument("name")
    p.add_argument("value", nargs="?")
    p.set_defaults(handler=cmd_user_attr)
    p = attr.add_parser("list", help="list a user's attributes")
    p.add_argument("username")
    p.set_defaults(handler=cmd_user_attr)


def _add_role_parsers(sub) -> None:
    role = sub.add_parser("role", help="roles").add_subparsers(dest="action", required=True)
    p = role.add_parser("add", help="add a role")
    p.add_argument("code")
    p.add_argument("--name")
    p.set_defaults(handler=cmd_role)
    role.add_parser("list", help="list roles and members").set_defaults(handler=cmd_role)
    member = role.add_parser("member", help="role members").add_subparsers(dest="member_action", required=True)
    for action in ("add", "remove"):
        p = member.add_parser(action, help=f"{action} a member")
        p.add_argument("code")
        p.add_argument("username")
        p.set_defaults(handler=cmd_role)


def _add_grant_parsers(sub) -> None:
    def principal(p):
        who = p.add_mutually_exclusive_group(required=True)
        who.add_argument("--user")
        who.add_argument("--role")

    grant = sub.add_parser("grant", help="grant a group, a report or a datasource").add_subparsers(dest="kind", required=True)
    for kind in ("group", "report", "datasource"):
        p = grant.add_parser(kind, help=f"grant a {kind} (by its code)")
        p.add_argument("code")
        principal(p)
        if kind in ("group", "report"):
            p.add_argument("--export", dest="export", action="store_true", default=None)
            p.add_argument("--no-export", dest="export", action="store_false")
            p.add_argument("--refresh", dest="refresh", action="store_true", default=None)
            p.add_argument("--no-refresh", dest="refresh", action="store_false")
        if kind == "group":
            p.add_argument("--design", dest="design", action="store_true", default=None)
            p.add_argument("--no-design", dest="design", action="store_false")
        p.set_defaults(handler=cmd_grant)
    p = grant.add_parser("show", help="the reports a user can view, and through which grant")
    p.add_argument("--user", required=True)
    p.set_defaults(handler=cmd_grant_show)

    revoke = sub.add_parser("revoke", help="remove a grant").add_subparsers(dest="kind", required=True)
    for kind in ("group", "report", "datasource"):
        p = revoke.add_parser(kind, help=f"revoke a {kind} grant")
        p.add_argument("code")
        principal(p)
        p.set_defaults(handler=cmd_revoke)


def main(argv: Sequence[str] | None = None) -> int:
    from drscore.logsetup import setup_logging

    args = build_parser().parse_args(argv)
    handler: Handler = args.handler
    try:
        if handler not in (cmd_secret_new_key, cmd_init):  # these two run before any configuration exists
            setup_logging(console=False)
        return handler(args)
    except (ConfigError, CommandError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except DRSError as exc:
        print(f"Error: {exc.code}: {exc.admin_detail or exc.detail or exc.message}", file=sys.stderr)
        return 1
