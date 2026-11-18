"""Spaces: create, rename, icon, order, archive, read state."""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import access as levels
from .. import schemas
from ..db.models import AgentConnection, Message, SessionBinding, Space
from ..errors import ApiError, conflict, invalid, missing, stale
from .base import DomainBase, new_id, now
from .views import space_out, spaces_out

GENERAL_KEY = "general_space_id"
PRIMARY_KEY = "primary_connection_id"


class SpacesMixin(DomainBase):
    def primary_connection_id(self, session: Session) -> str | None:
        return self.meta_get(session, PRIMARY_KEY)

    def general_space_id(self, session: Session) -> str:
        value = self.meta_get(session, GENERAL_KEY)
        assert value is not None
        return value

    def seed(self) -> None:
        """Create General and the primary connection once. Safe to run on every start."""
        with self.db.write() as session:
            if self.meta_get(session, "service_id") is None:
                self.meta_set(session, "service_id", new_id())
            primary = self.meta_get(session, PRIMARY_KEY)
            if primary is None or session.get(AgentConnection, primary) is None:
                timestamp = now()
                kind = "fake" if self.settings.provider == "fake" else "hermes"
                name = "Test agent" if kind == "fake" else "Hermes"
                connection = AgentConnection(
                    id=new_id(),
                    name=name,
                    kind=kind,
                    state="connected" if kind == "fake" else "disconnected",
                    config={},
                    capabilities={},
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                session.add(connection)
                session.flush()
                primary = connection.id
                self.meta_set(session, PRIMARY_KEY, primary)
            if self.meta_get(session, GENERAL_KEY) is None:
                timestamp = now()
                space = Space(
                    id=new_id(),
                    title="General",
                    icon_kind="catalog",
                    icon_value="general",
                    kind="general",
                    ordinal=0,
                    agent_connection_id=primary,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                session.add(space)
                session.flush()
                self.meta_set(session, GENERAL_KEY, space.id)

    def list_spaces(self, *, include_archived: bool = False) -> list[schemas.SpaceOut]:
        with self.db.read() as session:
            return self._spaces(session, include_archived)

    def _spaces(self, session: Session, include_archived: bool) -> list[schemas.SpaceOut]:
        query = select(Space).where(Space.deleted_at.is_(None))
        if not include_archived:
            query = query.where(Space.archived_at.is_(None))
        return spaces_out(session, session.scalars(query.order_by(Space.ordinal, Space.created_at)))

    def _next_ordinal(self, session: Session) -> int:
        return (session.scalar(select(func.max(Space.ordinal)).where(Space.deleted_at.is_(None))) or 0) + 1

    def _validate_directory(self, connection: AgentConnection, directory: str | None) -> str | None:
        if directory is None:
            return None
        roots = (connection.config or {}).get("allowed_roots") or [str(p) for p in self.settings.workspace_roots]
        from pathlib import Path, PurePosixPath

        if connection.kind == "bridge":
            # The folder is on the connector's host, which checks it again before every run.
            path = PurePosixPath(os.path.normpath(directory))
            if not path.is_absolute() or not any(path.is_relative_to(PurePosixPath(root)) for root in roots):
                raise invalid(
                    "directory_not_allowed", "That working directory is outside the allowed roots.", allowed=roots
                )
            return str(path)
        resolved = Path(directory).expanduser().resolve()
        if not resolved.is_dir():
            raise invalid("directory_missing", "That working directory does not exist.")
        if not any(resolved.is_relative_to(Path(root).expanduser().resolve()) for root in roots):
            raise invalid(
                "directory_not_allowed", "That working directory is outside the allowed roots.", allowed=roots
            )
        return str(resolved)

    def _validate_access(self, connection: AgentConnection, access: str | None) -> str | None:
        if access is None:
            return None
        if connection.kind == "bridge":
            top = (connection.capabilities or {}).get("max_access")
        elif connection.kind in ("codex", "claude"):
            top = levels.ceiling(connection.kind, connection.config or {})
        else:
            top = None
        if not levels.allows(top, access):
            raise invalid("access_not_allowed", f"{connection.name} does not allow {levels.NAMES[access]}.")
        return access

    def insert_space(
        self,
        session: Session,
        *,
        title: str,
        icon: schemas.Icon,
        kind: str,
        connection_id: str | None,
        working_directory: str | None = None,
        access: str | None = None,
    ) -> Space:
        connection_id = connection_id or self.primary_connection_id(session)
        connection = session.get(AgentConnection, connection_id) if connection_id else None
        if connection is None:
            raise invalid("connection_missing", "Choose a configured agent for this space.")
        directory = self._validate_directory(connection, working_directory)
        if kind == "code" and directory is None:
            raise invalid("directory_required", "Choose a working directory for this session.")
        access = self._validate_access(connection, access) if kind == "code" else None
        timestamp = now()
        space = Space(
            id=new_id(),
            title=title.strip()[:200],
            icon_kind=icon.kind,
            icon_value=icon.value,
            kind=kind,
            ordinal=self._next_ordinal(session),
            agent_connection_id=connection_id,
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add(space)
        session.flush()
        session.add(
            SessionBinding(
                id=new_id(),
                space_id=space.id,
                connection_id=connection_id,
                working_directory=directory,
                access=access,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        session.flush()
        self.emit(session, "space.created", space.id, {"space": space_out(session, space).model_dump()})
        return space

    def create_space(self, body: schemas.SpaceCreate, actor: str) -> tuple[schemas.SpaceOut, bool]:
        with self.db.write() as session:

            def action() -> dict[str, Any]:
                space = self.insert_space(
                    session,
                    title=body.title,
                    icon=body.icon,
                    kind=body.kind,
                    connection_id=body.agent_connection_id,
                    working_directory=body.working_directory,
                    access=body.access,
                )
                return {"space_id": space.id}

            result, duplicate = self.idempotent(
                session,
                "create_space",
                body.idempotency_key,
                actor,
                body.model_dump(exclude={"idempotency_key"}),
                action,
            )
            return space_out(session, self.space_or_404(session, result["space_id"])), duplicate

    def patch_space(self, space_id: str, body: schemas.SpacePatch) -> schemas.SpaceOut:
        with self.db.write() as session:
            space = self.space_or_404(session, space_id)
            if space.revision != body.expected_revision:
                raise stale("This space", space.revision)
            changed = False
            if body.title is not None and body.title.strip() != space.title:
                if space.kind == "general":
                    raise conflict("general_immutable", "General cannot be renamed.")
                space.title = body.title.strip()
                changed = True
            if body.icon is not None:
                if space.kind == "general":
                    raise conflict("general_immutable", "General keeps its icon.")
                space.icon_kind, space.icon_value = body.icon.kind, body.icon.value
                changed = True
            if body.ordinal is not None:
                if space.kind == "general":
                    raise conflict("general_immutable", "General stays first.")
                self._reorder(session, space, body.ordinal)
                changed = True
            if body.access is not None:
                binding = session.scalar(select(SessionBinding).where(SessionBinding.space_id == space.id))
                connection = session.get(AgentConnection, binding.connection_id) if binding else None
                if space.kind != "code" or binding is None or connection is None:
                    raise invalid("access_not_allowed", "Only coding sessions have an access level.")
                if binding.access != body.access:
                    binding.access = self._validate_access(connection, body.access)
                    binding.updated_at = now()
                    changed = True
            if changed:
                space.revision += 1
                space.updated_at = now()
                session.flush()
                self.emit(session, "space.updated", space.id, {"space": space_out(session, space).model_dump()})
            return space_out(session, space)

    def _reorder(self, session: Session, space: Space, index: int) -> None:
        peers = list(
            session.scalars(
                select(Space)
                .where(Space.deleted_at.is_(None), Space.archived_at.is_(None), Space.kind != "general")
                .where(Space.id != space.id)
                .order_by(Space.ordinal, Space.created_at)
            )
        )
        index = min(index, len(peers))
        peers.insert(index, space)
        for position, peer in enumerate(peers, start=1):
            if peer.ordinal != position:
                peer.ordinal = position
                if peer.id != space.id:
                    peer.revision += 1
                    peer.updated_at = now()
                    session.flush()
                    self.emit(session, "space.updated", peer.id, {"space": space_out(session, peer).model_dump()})

    def archive_space(self, space_id: str, *, restore: bool = False) -> schemas.SpaceOut:
        with self.db.write() as session:
            return self._archive(session, space_id, restore=restore)

    def _archive(self, session: Session, space_id: str, *, restore: bool = False) -> schemas.SpaceOut:
        space = self.space_or_404(session, space_id)
        if space.kind == "general":
            raise conflict("general_immutable", "General cannot be archived.")
        space.archived_at = None if restore else now()
        space.revision += 1
        space.updated_at = now()
        session.flush()
        out = space_out(session, space)
        self.emit(session, "space.archived", space.id, {"space": out.model_dump()})
        return out

    def mark_read(self, space_id: str) -> schemas.SpaceOut:
        with self.db.write() as session:
            space = self.space_or_404(session, space_id)
            if space.read_seq != space.last_message_seq:
                space.read_seq = space.last_message_seq
                session.flush()
                self.emit(session, "space.updated", space.id, {"space": space_out(session, space).model_dump()})
            return space_out(session, space)

    def touch_space(self, session: Session, space: Space, seq: int, *, read: bool = False) -> None:
        space.last_message_seq = seq
        if read:
            space.read_seq = seq

    def get_space(self, space_id: str) -> schemas.SpaceOut:
        with self.db.read() as session:
            return space_out(session, self.space_or_404(session, space_id))

    def tool_scope_space(self, session: Session, space_id: str | None) -> Space:
        if space_id is None:
            raise invalid("space_required", "A space is required.")
        try:
            return self.space_or_404(session, space_id)
        except ApiError as error:
            raise missing("Space") from error

    def message_exists(self, session: Session, message_id: str) -> Message | None:
        return session.get(Message, message_id)


__all__ = ["GENERAL_KEY", "PRIMARY_KEY", "SpacesMixin", "update"]
