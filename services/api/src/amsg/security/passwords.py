"""Password hashes: scrypt from the standard library, stored with their parameters so they can be raised later."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# 2^15 × 8 × 128 bytes = 32 MiB per check, about 100 ms: slow to guess offline, fine for one sign-in.
N, R, P = 2**15, 8, 1
MAX_MEMORY = 64 * 1024 * 1024
MIN_LENGTH = 12


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=MAX_MEMORY, dklen=32)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(_derive(password, salt, N, R, P))}"


def verify_password(password: str, stored: str | None) -> bool:
    """Always does the full derivation, so a missing or malformed hash takes as long as a wrong password."""
    try:
        scheme, n, r, p, salt, digest = (stored or "").split("$")
        if scheme != "scrypt":
            raise ValueError(scheme)
        params = int(n), int(r), int(p)
        expected = _unb64(digest)
        salt_bytes = _unb64(salt)
    except ValueError:
        params, expected, salt_bytes = (N, R, P), b"\0" * 32, b"\0" * 16
        _derive(password, salt_bytes, *params)
        return False
    return hmac.compare_digest(_derive(password, salt_bytes, *params), expected)
