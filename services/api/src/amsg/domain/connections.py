"""Agent connections, connector credentials and audited Hermes configuration patches."""

from __future__ import annotations

import difflib
import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from .. import schemas
from ..db.models import AgentConnection, ConfigPatch, ConnectionSecret
from ..errors import ApiError, conflict, invalid, missing
from .base import DomainBase, new_id, now
from .views import connection_out


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ConnectionsMixin(DomainBase):
    def list_connections(self) -> list[schemas.ConnectionOut]:
        with self.db.read() as session:
            rows = session.scalars(select(AgentConnection).order_by(AgentConnection.created_at))
            return [connection_out(session, row) for row in rows]

    def create_connection(self, body: schemas.ConnectionCreate) -> schemas.ConnectionOut:
        with self.db.write() as session:
            timestamp = now()
            connection = AgentConnection(
                id=new_id(),
                name=body.name.strip(),
                kind=body.kind,
                state="unknown",
                config=body.config,
                capabilities={},
                created_at=timestamp,
                updated_at=timestamp,
            )
            session.add(connection)
            session.flush()
            for name, value in body.secrets.items():
                session.add(ConnectionSecret(connection_id=connection.id, name=name[:64], value=value))
            session.flush()
            out = connection_out(session, connection)
            self.emit(session, "connection.updated", connection.id, {"connection": out.model_dump()})
            return out

    def set_connection_config(self, connection_id: str, **values: Any) -> None:
        with self.db.write() as session:
            connection = session.get(AgentConnection, connection_id)
            if connection is None:
                raise missing("Connection")
            connection.config = {**(connection.config or {}), **values}
            connection.revision += 1
            connection.updated_at = now()

    def get_connection(self, connection_id: str) -> AgentConnection:
        with self.db.read() as session:
            connection = session.get(AgentConnection, connection_id)
            if connection is None:
                raise missing("Connection")
            return connection

    def connection_secrets(self, connection_id: str) -> dict[str, str]:
        with self.db.read() as session:
            rows = session.scalars(select(ConnectionSecret).where(ConnectionSecret.connection_id == connection_id))
            return {row.name: row.value for row in rows}

    def update_connection_state(
        self,
        connection_id: str,
        state: str,
        *,
        capabilities: dict[str, Any] | None = None,
        seen: bool = False,
        checked: bool = False,
    ) -> None:
        with self.db.write() as session:
            connection = session.get(AgentConnection, connection_id)
            if connection is None:
                return
            changed = connection.state != state
            connection.state = state
            timestamp = now()
            if capabilities is not None and capabilities != connection.capabilities:
                connection.capabilities = capabilities
                changed = True
            if seen:
                connection.last_seen_at = timestamp
            if checked:
                connection.last_checked_at = timestamp
                changed = True
            if changed:
                connection.revision += 1
                connection.updated_at = timestamp
                session.flush()
                self.emit(
                    session,
                    "connection.updated",
                    connection.id,
                    {"connection": connection_out(session, connection).model_dump()},
                )

    # Configuration patches ---------------------------------------------------------------------------
    def _config_path(self, connection: AgentConnection) -> Path:
        configured = (connection.config or {}).get("config_path")
        if not configured:
            raise invalid("config_unavailable", "This connection has no configuration file set.")
        return Path(configured).expanduser()

    def read_config(self, connection_id: str) -> schemas.ConfigFile:
        path = self._config_path(self.get_connection(connection_id))
        try:
            data = path.read_bytes()
        except OSError as error:
            raise invalid("config_unavailable", "The configuration file cannot be read.") from error
        return schemas.ConfigFile(path=str(path), content=data.decode(), checksum=checksum(data))

    def propose_patch(self, connection_id: str, body: schemas.ConfigPatchCreate) -> schemas.ConfigPatchOut:
        connection = self.get_connection(connection_id)
        path = self._config_path(connection)
        if Path(body.path).expanduser() != path:
            raise invalid("config_path_mismatch", "Patches can only target the connection's configuration file.")
        current = path.read_bytes()
        if checksum(current) != body.base_checksum:
            raise conflict("stale_base", "The configuration changed after this patch was prepared.")
        if path.suffix in (".yaml", ".yml"):
            try:
                yaml.safe_load(body.proposed)
            except yaml.YAMLError as error:
                raise invalid("config_syntax", f"The proposed configuration is not valid YAML: {error}") from error
        diff = "".join(
            difflib.unified_diff(
                current.decode().splitlines(keepends=True),
                body.proposed.splitlines(keepends=True),
                fromfile=path.name,
                tofile=f"{path.name} (proposed)",
            )
        )
        with self.db.write() as session:
            timestamp = now()
            patch = ConfigPatch(
                id=new_id(),
                connection_id=connection_id,
                path=str(path),
                base_checksum=body.base_checksum,
                proposed=body.proposed,
                diff=diff,
                created_at=timestamp,
                updated_at=timestamp,
            )
            session.add(patch)
            session.flush()
            return schemas.ConfigPatchOut.model_validate(patch)

    def get_patch(self, patch_id: str) -> ConfigPatch:
        with self.db.read() as session:
            patch = session.get(ConfigPatch, patch_id)
            if patch is None:
                raise missing("Configuration patch")
            return patch

    def _set_patch(self, patch_id: str, state: str, **fields: Any) -> schemas.ConfigPatchOut:
        with self.db.write() as session:
            patch = session.get(ConfigPatch, patch_id)
            assert patch is not None
            patch.state = state
            patch.updated_at = now()
            for key, value in fields.items():
                setattr(patch, key, value)
            session.flush()
            return schemas.ConfigPatchOut.model_validate(patch)

    def apply_patch(self, patch_id: str) -> schemas.ConfigPatchOut:
        """Device-scope only. Backup, atomic replace, supported reload, health check, then offer rollback."""
        patch = self.get_patch(patch_id)
        if patch.state != "proposed":
            raise conflict("patch_closed", "This patch has already been applied or closed.")
        path = Path(patch.path)
        current = path.read_bytes()
        if checksum(current) != patch.base_checksum:
            self._set_patch(patch_id, "rejected")
            raise conflict("stale_base", "The configuration changed after this patch was prepared.")
        backups = self.settings.data_dir / "config-backups"
        backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = backups / f"{patch_id}{path.suffix}"
        backup.write_bytes(current)
        backup.chmod(0o600)
        self._atomic_write(path, patch.proposed.encode())
        health = self._health_check(patch.connection_id)
        state = "applied" if health["ok"] else "unhealthy"
        return self._set_patch(patch_id, state, backup_path=str(backup), health=health)

    def rollback_patch(self, patch_id: str) -> schemas.ConfigPatchOut:
        patch = self.get_patch(patch_id)
        if patch.state not in ("applied", "unhealthy") or not patch.backup_path:
            raise conflict("patch_closed", "There is no applied change to restore.")
        self._atomic_write(Path(patch.path), Path(patch.backup_path).read_bytes())
        return self._set_patch(patch_id, "rolled_back", health=self._health_check(patch.connection_id))

    def _atomic_write(self, path: Path, data: bytes) -> None:
        temporary = path.with_name(f".{path.name}.amsg-{os.getpid()}")
        mode = path.stat().st_mode if path.exists() else 0o600
        temporary.write_bytes(data)
        temporary.chmod(mode)
        os.replace(temporary, path)

    def _health_check(self, connection_id: str) -> dict[str, Any]:
        connection = self.get_connection(connection_id)
        command = (connection.config or {}).get("reload_command")
        if not command:
            return {"ok": True, "detail": "No reload command is set for this connection."}
        if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
            raise ApiError(422, "config_unavailable", "The reload command must be an argument list.")
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=60)  # noqa: S603
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"ok": False, "detail": str(error)[:500]}
        return {"ok": done.returncode == 0, "detail": (done.stdout + done.stderr)[-500:]}
