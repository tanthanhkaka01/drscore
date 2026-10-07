"""Password hashing with Argon2id (spec section 9.1), through argon2-cffi with its defaults."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # argon2-cffi defaults: Argon2id, RFC 9106 low-memory profile

# Verified against when the user name is unknown, so both failures take the same time.
_DUMMY_HASH = _hasher.hash("drs-dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when the hash was made with older parameters than the current defaults."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True
