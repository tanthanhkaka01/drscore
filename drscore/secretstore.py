"""Secrets of DRS's own integrations, kept encrypted in the DRS database (``drs_secret``).

The AI agent's API key (``ai.api_key``) and the bot tokens (``telegram.bot_token``,
``zalo.bot_token``) are never written in a file in clear. Each is read, in this order:

1. the environment variable named in app.toml (``[ai] api_key_env``, ``[notify.telegram] bot_token_env``, ...),
   when it is set - convenient on a build PC;
2. else the row of ``drs_secret``: ``enc:<Fernet token>``, encrypted with the key in ``DRS_SECRET_KEY``
   (``app.secret_key_env``), the same key and the same scheme as the datasource passwords.

On a server the second way is the one to use: the database holds only the ciphertext, the
environment only the encryption key, and no file holds the secret. ``python -m drscore secret set``
stores one without ever printing it.
"""

from __future__ import annotations

import logging
import os
import time

from cryptography.fernet import InvalidToken
from sqlalchemy import select

from drscore.datasources.secrets import _fernet
from drscore.db.models import Secret

log = logging.getLogger(__name__)

# The secrets DRS knows, with what each one is for.
KNOWN = {
    "ai.api_key": "the Anthropic API key of the AI agent ([ai])",
    "telegram.bot_token": "the Telegram bot token ([notify.telegram])",
    "zalo.bot_token": "the Zalo bot token ([notify.zalo])",
}

# A value read from the database is kept this long, so a busy page does not decrypt it every time.
_KEEP_SECONDS = 60
_kept: dict[str, tuple[float, str | None]] = {}


def encrypt(value: str) -> str:
    return "enc:" + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(secret_ref: str) -> str:
    if not secret_ref.startswith("enc:"):
        raise ValueError("a stored secret must start with enc:")
    return _fernet().decrypt(secret_ref[4:].encode("ascii")).decode("utf-8")


def forget() -> None:
    """Drops the values kept in memory (after `secret set`, and in tests)."""
    _kept.clear()


def get(name: str, env_name: str | None = None, database=None) -> str | None:
    """The secret's value, or None when it is not set (or cannot be decrypted: logged, not raised)."""
    if env_name:
        value = os.environ.get(env_name)
        if value:
            return value
    now = time.monotonic()
    kept = _kept.get(name)
    if kept and now - kept[0] < _KEEP_SECONDS:
        return kept[1]
    value = None
    try:
        if database is None:
            from drscore.db import get_database

            database = get_database()
        with database.session() as s:
            row = s.get(Secret, name)
            ref = row.secret_ref if row else None
        if ref:
            value = decrypt(ref)
    except InvalidToken:
        log.error("The secret %s cannot be decrypted with the current %s", name, "encryption key")
    except Exception as exc:  # no key set, no table yet: the integration stays off
        log.warning("The secret %s is not available: %s", name, exc.__class__.__name__)
    _kept[name] = (now, value)
    return value


def put(session, name: str, value: str, actor: str) -> None:
    """Stores (or replaces) a secret, encrypted. The caller commits."""
    ref = encrypt(value)
    row = session.get(Secret, name)
    if row is None:
        session.add(Secret(secret_name=name, secret_ref=ref, updated_by=actor[:100]))
    else:
        row.secret_ref = ref
        row.updated_by = actor[:100]
    forget()


def remove(session, name: str) -> bool:
    row = session.get(Secret, name)
    if row is None:
        return False
    session.delete(row)
    forget()
    return True


def listing(session) -> list[Secret]:
    return list(session.scalars(select(Secret).order_by(Secret.secret_name)))
