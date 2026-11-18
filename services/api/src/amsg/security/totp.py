"""Time-based one-time codes (RFC 6238): SHA-1, six digits, 30-second steps, as authenticator apps expect."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP = 30
DIGITS = 6
# Codes from one step either side are accepted, for clock drift and the time taken to type.
WINDOW = 1


def new_secret() -> str:
    """160 random bits in unpadded base32, the form authenticator apps take."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def normalize(secret: str) -> str:
    """A supplied secret in canonical form. At least 128 bits, as RFC 4226 requires."""
    cleaned = secret.replace(" ", "").upper().rstrip("=")
    if len(cleaned) < 26 or set(cleaned) - set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"):
        raise ValueError("The authenticator secret must be base32 with at least 128 bits.")
    _key(cleaned)
    return cleaned


def _key(secret: str) -> bytes:
    cleaned = secret.replace(" ", "").upper()
    return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))


def code_at(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def current_step(moment: float | None = None) -> int:
    return int((time.time() if moment is None else moment) // STEP)


def match(secret: str, code: str, moment: float | None = None) -> int | None:
    """The step the code belongs to, or None. Every candidate is compared, so timing does not reveal which."""
    cleaned = "".join(ch for ch in code if ch.isdigit())
    if len(cleaned) != DIGITS:
        return None
    step = current_step(moment)
    found = None
    for candidate in range(step - WINDOW, step + WINDOW + 1):
        if hmac.compare_digest(code_at(secret, candidate), cleaned):
            found = candidate
    return found


def provisioning_uri(secret: str, account: str, issuer: str = "a-msg") -> str:
    label = quote(f"{issuer}:{account}")
    query = urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": DIGITS, "period": STEP})
    return f"otpauth://totp/{label}?{query}"
