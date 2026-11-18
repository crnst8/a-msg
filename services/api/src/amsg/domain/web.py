"""Browser sign-in: one password and one authenticator code make a device credential held in a session cookie.

A browser session is an ordinary device credential marked `client = "web"`, so it is listed and revoked with the
paired devices. It ends after WEB_SESSION_DAYS, or after WEB_IDLE_DAYS without use, and then needs both factors again.
"""

from __future__ import annotations

from sqlalchemy import select

from ..db.models import DeviceCredential
from ..errors import ApiError
from ..security import passwords, tokens, totp
from .base import DomainBase, new_id, now

WEB_SESSION_DAYS = 90
WEB_IDLE_DAYS = 30
PASSWORD_KEY = "web.password"  # noqa: S105 - a meta key, not a password
TOTP_KEY = "web.totp_secret"
TOTP_STEP_KEY = "web.totp_last_step"


class WebMixin(DomainBase):
    def web_configured(self) -> bool:
        with self.db.read() as session:
            return bool(self.meta_get(session, PASSWORD_KEY) and self.meta_get(session, TOTP_KEY))

    def web_set_password(self, password: str) -> None:
        if len(password) < passwords.MIN_LENGTH:
            raise ValueError(f"Use at least {passwords.MIN_LENGTH} characters.")
        digest = passwords.hash_password(password)
        with self.db.write() as session:
            self.meta_set(session, PASSWORD_KEY, digest)

    def web_new_totp(self, secret: str | None = None) -> str:
        """A new authenticator secret, generated here or supplied. Codes from the previous one stop working."""
        secret = totp.normalize(secret) if secret else totp.new_secret()
        with self.db.write() as session:
            self.meta_set(session, TOTP_KEY, secret)
            self.meta_set(session, TOTP_STEP_KEY, "0")
        return secret

    def web_sign_in(self, password: str, code: str, device_name: str) -> tuple[str, str]:
        """Returns (token, credential id). Both factors are always checked, so the reply never says which failed."""
        with self.db.read() as session:
            stored = self.meta_get(session, PASSWORD_KEY)
            secret = self.meta_get(session, TOTP_KEY)
        password_ok = passwords.verify_password(password, stored)
        step = totp.match(secret, code) if secret else None
        if not (password_ok and step is not None):
            raise ApiError(401, "sign_in_invalid", "The password or code is wrong.")
        with self.db.write() as session:
            # A code works once: a code seen in transit or over a shoulder cannot be replayed within its window.
            last = int(self.meta_get(session, TOTP_STEP_KEY) or "0")
            if step <= last:
                raise ApiError(401, "code_used", "That code was already used. Wait for the next one.")
            self.meta_set(session, TOTP_STEP_KEY, str(step))
            credential_id = new_id()
            token = tokens.new_token(credential_id)
            session.add(
                DeviceCredential(
                    id=credential_id,
                    name=device_name[:120] or "Browser",
                    token_hash=tokens.hash_secret(token),
                    scope="device",
                    client="web",
                    created_at=now(),
                    last_seen_at=now(),
                )
            )
        return token, credential_id

    def web_revoke_all(self) -> int:
        with self.db.write() as session:
            rows = session.scalars(
                select(DeviceCredential).where(DeviceCredential.client == "web", DeviceCredential.revoked_at.is_(None))
            ).all()
            for row in rows:
                row.revoked_at = now()
            return len(rows)
