"""Pairing, credentials and principals. Connector tokens are bound to one connection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .. import __version__, schemas
from ..db.models import AgentConnection, DeviceCredential, PairingToken
from ..errors import ApiError, missing
from ..security import tokens
from .base import DomainBase, later, new_id, now

PAIRING_TTL_SECONDS = 600
LAST_SEEN_INTERVAL = timedelta(seconds=60)


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _web_expired(created: str, seen: str | None, moment: datetime) -> bool:
    from .web import WEB_IDLE_DAYS, WEB_SESSION_DAYS

    if moment - _parse(created) > timedelta(days=WEB_SESSION_DAYS):
        return True
    return seen is not None and moment - _parse(seen) > timedelta(days=WEB_IDLE_DAYS)


@dataclass(frozen=True, slots=True)
class Principal:
    id: str
    scope: str
    connection_id: str | None = None
    client: str | None = None

    @property
    def actor(self) -> str:
        return f"{self.scope}:{self.id}"


class AuthMixin(DomainBase):
    def issue_pairing_code(self) -> tuple[str, str]:
        code = tokens.new_pairing_code()
        expires = later(PAIRING_TTL_SECONDS)
        with self.db.write() as session:
            session.add(
                PairingToken(id=new_id(), code_hash=tokens.hash_secret(code), expires_at=expires, created_at=now())
            )
        return code, expires

    def pair(self, body: schemas.PairRequest) -> schemas.PairResult:
        digest = tokens.hash_secret(tokens.normalize_code(body.code))
        with self.db.write() as session:
            row = session.scalar(select(PairingToken).where(PairingToken.code_hash == digest))
            if row is None or row.used_at is not None or row.expires_at < now():
                raise ApiError(401, "pairing_invalid", "That pairing code is wrong, used or expired.")
            row.used_at = now()
            credential_id = new_id()
            token = tokens.new_token(credential_id)
            session.add(
                DeviceCredential(
                    id=credential_id,
                    name=body.device_name,
                    token_hash=tokens.hash_secret(token),
                    scope="device",
                    created_at=now(),
                )
            )
            service_id = self.meta_get(session, "service_id")
            assert service_id is not None
            return schemas.PairResult(
                token=token, device_id=credential_id, service_id=service_id, service_version=__version__
            )

    def issue_device_token(self, name: str) -> str:
        with self.db.write() as session:
            credential_id = new_id()
            token = tokens.new_token(credential_id)
            session.add(
                DeviceCredential(
                    id=credential_id,
                    name=name,
                    token_hash=tokens.hash_secret(token),
                    scope="device",
                    created_at=now(),
                )
            )
            return token

    def issue_connector_token(self, connection_id: str, name: str = "connector") -> str:
        with self.db.write() as session:
            if session.get(AgentConnection, connection_id) is None:
                raise missing("Connection")
            credential_id = new_id()
            token = tokens.new_token(credential_id)
            session.add(
                DeviceCredential(
                    id=credential_id,
                    name=name,
                    token_hash=tokens.hash_secret(token),
                    scope="connector",
                    connection_id=connection_id,
                    created_at=now(),
                )
            )
            return token

    def issue_runner_token(self, name: str) -> str:
        """The connection for a remote coding agent, created on first use. Earlier credentials stop working."""
        existing = next((c for c in self.list_connections() if c.kind == "bridge" and c.name == name), None)
        connection = existing or self.create_connection(schemas.ConnectionCreate(name=name, kind="bridge"))
        with self.db.write() as session:
            current = select(DeviceCredential).where(
                DeviceCredential.connection_id == connection.id, DeviceCredential.revoked_at.is_(None)
            )
            for credential in session.scalars(current):
                credential.revoked_at = now()
        return self.issue_connector_token(connection.id, f"runner: {name}")

    def authenticate(self, token: str) -> Principal | None:
        credential_id = tokens.parse_token(token)
        if credential_id is None:
            return None
        with self.db.read() as session:
            credential = session.get(DeviceCredential, credential_id)
            if credential is None or credential.revoked_at is not None:
                return None
            if not tokens.matches(token, credential.token_hash):
                return None
            principal = Principal(credential.id, credential.scope, credential.connection_id, credential.client)
            seen = credential.last_seen_at
            created = credential.created_at
        moment = datetime.now(UTC)
        if principal.client == "web" and _web_expired(created, seen, moment):
            with self.db.write() as session:
                row = session.get(DeviceCredential, credential_id)
                if row is not None:
                    row.revoked_at = row.revoked_at or now()
            return None
        if seen is None or moment - _parse(seen) > LAST_SEEN_INTERVAL:
            with self.db.write() as session:
                row = session.get(DeviceCredential, credential_id)
                if row is not None:
                    row.last_seen_at = now()
        return principal

    def list_devices(self, current_id: str) -> list[schemas.DeviceOut]:
        with self.db.read() as session:
            rows = session.scalars(
                select(DeviceCredential).where(DeviceCredential.scope == "device").order_by(DeviceCredential.created_at)
            )
            return [
                schemas.DeviceOut(
                    id=row.id,
                    name=row.name,
                    created_at=row.created_at,
                    last_seen_at=row.last_seen_at,
                    revoked_at=row.revoked_at,
                    current=row.id == current_id,
                )
                for row in rows
            ]

    def revoke(self, credential_id: str) -> None:
        with self.db.write() as session:
            row = session.get(DeviceCredential, credential_id)
            if row is None:
                raise missing("Device")
            row.revoked_at = row.revoked_at or now()
