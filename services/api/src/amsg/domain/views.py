"""Record-to-contract conversion, loading related rows in bulk to keep pages bounded."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import schemas
from ..db.models import (
    AgentConnection,
    Artifact,
    AutomationLink,
    ConnectionSecret,
    Message,
    MessageArtifact,
    Operation,
    Run,
    SessionBinding,
    SourceLink,
    Space,
)

PREVIEW_PREFIXES = ("image/", "text/", "audio/", "video/")
PREVIEW_TYPES = {"application/pdf", "application/json", "application/x-yaml", "application/xml"}
UNSAFE_IMAGE_TYPES = {"image/svg+xml"}


def previewable(mime: str) -> bool:
    if mime in UNSAFE_IMAGE_TYPES:
        return True  # shown as text, never rendered
    return mime in PREVIEW_TYPES or mime.startswith(PREVIEW_PREFIXES)


def artifact_ref(artifact: Artifact) -> schemas.ArtifactRef:
    return schemas.ArtifactRef(
        id=artifact.id,
        display_name=artifact.display_name,
        mime=artifact.mime,
        size=artifact.size,
        checksum=artifact.checksum,
        version=artifact.version,
        state=artifact.state,
        hidden=artifact.hidden_at is not None,
        previewable=artifact.state == "ready" and previewable(artifact.mime),
    )


def run_out(run: Run) -> schemas.RunOut:
    return schemas.RunOut.model_validate(run)


def messages_out(session: Session, messages: Iterable[Message]) -> list[schemas.MessageOut]:
    rows = list(messages)
    ids = [m.id for m in rows]
    if not ids:
        return []
    artifacts: dict[str, list[schemas.ArtifactRef]] = defaultdict(list)
    query = (
        select(MessageArtifact.message_id, Artifact)
        .join(Artifact, Artifact.id == MessageArtifact.artifact_id)
        .where(MessageArtifact.message_id.in_(ids))
        .order_by(MessageArtifact.ordinal)
    )
    for message_id, artifact in session.execute(query):
        artifacts[message_id].append(artifact_ref(artifact))

    links: dict[str, list[schemas.MessageLink]] = defaultdict(list)
    query = (
        select(SourceLink.source_message_id, Space)
        .join(Space, Space.id == SourceLink.dest_space_id)
        .where(SourceLink.source_message_id.in_(ids))
    )
    seen: set[tuple[str, str]] = set()
    for message_id, space in session.execute(query):
        if (message_id, space.id) in seen:
            continue
        seen.add((message_id, space.id))
        links[message_id].append(
            schemas.MessageLink(
                direction="continued_in",
                space_id=space.id,
                title=space.title,
                available=space.deleted_at is None,
            )
        )
    for message in rows:
        origin = (message.meta or {}).get("source_space_id")
        if message.role == "context" and origin:
            space = session.get(Space, origin)
            if space is not None:
                links[message.id].append(
                    schemas.MessageLink(
                        direction="from",
                        space_id=space.id,
                        title=space.title,
                        available=space.deleted_at is None,
                    )
                )

    run_ids = {m.run_id for m in rows if m.run_id}
    runs = {r.id: r for r in session.scalars(select(Run).where(Run.id.in_(run_ids)))} if run_ids else {}
    out = []
    for message in rows:
        run = runs.get(message.run_id or "")
        is_reply = run is not None and run.reply_message_id == message.id
        out.append(
            schemas.MessageOut(
                id=message.id,
                seq=message.seq,
                space_id=message.space_id,
                role=message.role,  # type: ignore[arg-type]
                markdown=message.markdown,
                status=message.status,  # type: ignore[arg-type]
                client_message_id=message.client_message_id,
                run_id=message.run_id,
                author=message.author,
                activity=message.activity,
                attempt=run.attempt if run else 1,
                error_code=run.error_code if is_reply else None,
                error_message=run.error_message if is_reply else None,
                revision=message.revision,
                meta=message.meta or {},
                artifacts=artifacts.get(message.id, []),
                links=links.get(message.id, []),
                created_at=message.created_at,
                updated_at=message.updated_at,
            )
        )
    return out


def message_out(session: Session, message: Message) -> schemas.MessageOut:
    return messages_out(session, [message])[0]


def spaces_out(session: Session, spaces: Iterable[Space]) -> list[schemas.SpaceOut]:
    rows = list(spaces)
    ids = [s.id for s in rows]
    if not ids:
        return []
    bindings = {b.space_id: b for b in session.scalars(select(SessionBinding).where(SessionBinding.space_id.in_(ids)))}
    counts = dict(
        session.execute(
            select(AutomationLink.space_id, func.count())
            .where(AutomationLink.space_id.in_(ids), AutomationLink.state != "removed")
            .group_by(AutomationLink.space_id)
        ).all()
    )
    pending = {
        op.entity_id: op.id
        for op in session.scalars(
            select(Operation).where(
                Operation.kind == "delete_space",
                Operation.entity_id.in_(ids),
                Operation.state.not_in(("space_deleted", "archived", "cancelled")),
            )
        )
    }
    out = []
    for space in rows:
        binding = bindings.get(space.id)
        out.append(
            schemas.SpaceOut(
                id=space.id,
                title=space.title,
                icon=schemas.Icon(kind=space.icon_kind, value=space.icon_value),  # type: ignore[arg-type]
                kind=space.kind,  # type: ignore[arg-type]
                ordinal=space.ordinal,
                agent_connection_id=space.agent_connection_id,
                archived_at=space.archived_at,
                unread=space.last_message_seq > space.read_seq,
                created_at=space.created_at,
                updated_at=space.updated_at,
                revision=space.revision,
                session=schemas.SessionInfo.model_validate(binding) if binding else None,
                automation_count=counts.get(space.id, 0),
                pending_operation_id=pending.get(space.id),
            )
        )
    return out


def space_out(session: Session, space: Space) -> schemas.SpaceOut:
    return spaces_out(session, [space])[0]


def connection_out(session: Session, connection: AgentConnection) -> schemas.ConnectionOut:
    has_secret = (
        session.scalar(select(ConnectionSecret.name).where(ConnectionSecret.connection_id == connection.id)) is not None
    )
    return schemas.ConnectionOut(
        id=connection.id,
        name=connection.name,
        kind=connection.kind,
        state=connection.state,
        config=connection.config or {},
        capabilities=connection.capabilities or {},
        last_checked_at=connection.last_checked_at,
        last_seen_at=connection.last_seen_at,
        has_secret=has_secret,
        revision=connection.revision,
        development_only=connection.kind == "fake",
    )
