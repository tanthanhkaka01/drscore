"""Datasource passwords (spec sections 6.3 and 11.1).

``drs_datasource.secret_ref`` is never a plain password. It is either ``env:NAME`` (the password
is in the environment variable NAME) or ``enc:<Fernet token>`` (encrypted with the key in the
environment variable named by ``app.secret_key_env``).
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

from drscore.errors import DRSError
from drscore.settings import ConfigError, get_settings


def _fernet() -> Fernet:
    name = get_settings().app.app.secret_key_env
    key = os.environ.get(name)
    if not key:
        raise ConfigError(f"The encryption key is not set: define the environment variable {name} "
                          f"(python -m drscore secret new-key prints one).")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ConfigError(f"The environment variable {name} does not hold a valid key.") from exc


def encrypt(password: str) -> str:
    return "enc:" + _fernet().encrypt(password.encode("utf-8")).decode("ascii")


def resolve(secret_ref: str | None, datasource_code: str) -> str | None:
    """The password a datasource connects with, or None when it has none."""
    if not secret_ref:
        return None
    if secret_ref.startswith("env:"):
        name = secret_ref[4:]
        value = os.environ.get(name)
        if value is None:
            raise DRSError("DATASOURCE_UNAVAILABLE",
                           admin_detail=f"datasource {datasource_code}: environment variable {name} is not set")
        return value
    if secret_ref.startswith("enc:"):
        try:
            return _fernet().decrypt(secret_ref[4:].encode("ascii")).decode("utf-8")
        except ConfigError as exc:
            raise DRSError("DATASOURCE_UNAVAILABLE", admin_detail=str(exc)) from exc
        except InvalidToken as exc:
            raise DRSError("DATASOURCE_UNAVAILABLE",
                           admin_detail=f"datasource {datasource_code}: the password cannot be decrypted "
                                        f"with the current key") from exc
    raise DRSError("REPORT_MISCONFIGURED",
                   admin_detail=f"datasource {datasource_code}: secret_ref must start with env: or enc:")
