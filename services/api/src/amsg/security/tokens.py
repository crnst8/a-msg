"""Device and connector credentials: 256-bit random tokens, stored only as hashes, compared in constant time."""

from __future__ import annotations

import hashlib
import hmac
import secrets

ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_token(credential_id: str) -> str:
    return f"amsg_{credential_id}.{secrets.token_urlsafe(32)}"


def parse_token(token: str) -> str | None:
    if not token.startswith("amsg_") or "." not in token:
        return None
    return token[5:].split(".", 1)[0]


def matches(token: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(token), stored_hash)


def new_pairing_code() -> str:
    """Twelve characters from a 30-symbol alphabet: 58 bits, typed or pasted once within ten minutes."""
    raw = "".join(secrets.choice(ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def normalize_code(code: str) -> str:
    cleaned = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{cleaned[:4]}-{cleaned[4:8]}-{cleaned[8:]}"
