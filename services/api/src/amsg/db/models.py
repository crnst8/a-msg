"""Authoritative records. IDs are UUID strings; timestamps are UTC ISO 8601 strings."""

from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Id = String(36)


class Base(DeclarativeBase):
    pass


class Timestamps:
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


class AgentConnection(Timestamps, Base):
    __tablename__ = "agent_connections"
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32))  # hermes | fake | codex | claude | bridge
    state: Mapped[str] = mapped_column(String(32), default="unknown")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    last_checked_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_seen_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class ConnectionSecret(Base):
    """Provider secrets live apart from the connection record, which is returned to clients."""

    __tablename__ = "connection_secrets"
    connection_id: Mapped[str] = mapped_column(
        Id, ForeignKey("agent_connections.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Space(Timestamps, Base):
    __tablename__ = "spaces"
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    icon_kind: Mapped[str] = mapped_column(String(16), default="catalog")
    icon_value: Mapped[str] = mapped_column(String(64), default="thread")
    kind: Mapped[str] = mapped_column(String(16))  # general | thread | code
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    agent_connection_id: Mapped[str | None] = mapped_column(
        Id, ForeignKey("agent_connections.id", ondelete="SET NULL"), nullable=True
    )
    archived_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deleted_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_message_seq: Mapped[int] = mapped_column(Integer, default=0)
    read_seq: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (Index("ix_spaces_order", "deleted_at", "archived_at", "ordinal"),)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    space_id: Mapped[str] = mapped_column(Id, ForeignKey("spaces.id", ondelete="CASCADE"))
    request_message_id: Mapped[str | None] = mapped_column(Id, nullable=True)
    reply_message_id: Mapped[str | None] = mapped_column(Id, nullable=True)
    provider_run_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # queued | running | cancel_requested | completed | failed | cancelled | delivery_uncertain
    state: Mapped[str] = mapped_column(String(32), default="queued")
    attempt_of: Mapped[str | None] = mapped_column(Id, ForeignKey("runs.id", ondelete="SET NULL"), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_at: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        Index("ix_runs_space_state", "space_id", "state"),
        Index("ix_runs_state", "state"),
    )


class Message(Timestamps, Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, unique=True)
    space_id: Mapped[str] = mapped_column(Id, ForeignKey("spaces.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))  # user | agent | context | event
    markdown: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32))
    client_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    run_id: Mapped[str | None] = mapped_column(Id, ForeignKey("runs.id", ondelete="SET NULL"), nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(
        Id, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    author: Mapped[str | None] = mapped_column(String(120), nullable=True)
    activity: Mapped[str | None] = mapped_column(String(300), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (Index("ix_messages_space_seq", "space_id", "seq"),)


class Artifact(Timestamps, Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(127))
    size: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    origin_message_id: Mapped[str | None] = mapped_column(
        Id, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    origin_space_id: Mapped[str | None] = mapped_column(Id, ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="upload")  # upload | agent
    state: Mapped[str] = mapped_column(String(16), default="ready")  # ready | deleted
    hidden_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deleted_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        Index("ix_artifacts_recent", "state", "hidden_at", "updated_at"),
        Index("ix_artifacts_group", "origin_space_id", "display_name"),
    )


class ArtifactVersion(Base):
    __tablename__ = "artifact_versions"
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    artifact_id: Mapped[str] = mapped_column(Id, ForeignKey("artifacts.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(32))
    __table_args__ = (UniqueConstraint("artifact_id", "version"),)


class MessageArtifact(Base):
    __tablename__ = "message_artifacts"
    message_id: Mapped[str] = mapped_column(Id, ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(Id, ForeignKey("artifacts.id", ondelete="RESTRICT"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (Index("ix_message_artifacts_artifact", "artifact_id"),)


class SourceLink(Base):
    __tablename__ = "source_links"
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    source_space_id: Mapped[str] = mapped_column(Id, ForeignKey("spaces.id", ondelete="CASCADE"))
    source_message_id: Mapped[str | None] = mapped_column(
        Id, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    dest_space_id: Mapped[str] = mapped_column(Id, ForeignKey("spaces.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(16))
    snapshot: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))
    __table_args__ = (
        Index("ix_source_links_dest", "dest_space_id", "ordinal"),
        Index("ix_source_links_source", "source_message_id"),
    )


class SessionBinding(Timestamps, Base):
    __tablename__ = "session_bindings"
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    space_id: Mapped[str] = mapped_column(Id, ForeignKey("spaces.id", ondelete="CASCADE"), unique=True)
    connection_id: Mapped[str] = mapped_column(Id, ForeignKey("agent_connections.id", ondelete="CASCADE"))
    provider_session_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    working_directory: Mapped[str | None] = mapped_column(Text, nullable=True)
    access: Mapped[str | None] = mapped_column(String(16), nullable=True)  # None: the connection's default
    state: Mapped[str] = mapped_column(String(16), default="active")


class AutomationLink(Timestamps, Base):
    __tablename__ = "automation_links"
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    connection_id: Mapped[str] = mapped_column(Id, ForeignKey("agent_connections.id", ondelete="CASCADE"))
    provider_job_id: Mapped[str] = mapped_column(String(200))
    space_id: Mapped[str | None] = mapped_column(Id, ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    schedule: Mapped[str] = mapped_column(String(200), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    next_run_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_run_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="active")  # active | paused | removed
    last_synced_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        UniqueConstraint("connection_id", "provider_job_id"),
        Index("ix_automation_links_space", "space_id"),
    )


class DeviceCredential(Base):
    __tablename__ = "device_credentials"
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(16))  # device | connector
    client: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "web" for browser sign-ins
    connection_id: Mapped[str | None] = mapped_column(
        Id, ForeignKey("agent_connections.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[str] = mapped_column(String(32))
    last_seen_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    revoked_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class PairingToken(Base):
    __tablename__ = "pairing_tokens"
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[str] = mapped_column(String(32))
    used_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))


class ActionRequest(Base):
    __tablename__ = "action_requests"
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    operation: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="completed")
    created_at: Mapped[str] = mapped_column(String(32))
    __table_args__ = (UniqueConstraint("operation", "idempotency_key"),)


class Event(Base):
    __tablename__ = "events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    schema_version: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(48))
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(32))
    __table_args__ = (Index("ix_events_created", "created_at"),)


class DispatchJob(Timestamps, Base):
    __tablename__ = "dispatch_jobs"
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="message")  # message | cancel | command
    space_id: Mapped[str | None] = mapped_column(Id, ForeignKey("spaces.id", ondelete="CASCADE"), nullable=True)
    run_id: Mapped[str | None] = mapped_column(Id, ForeignKey("runs.id", ondelete="CASCADE"), nullable=True)
    message_id: Mapped[str | None] = mapped_column(Id, nullable=True)
    connection_id: Mapped[str | None] = mapped_column(
        Id, ForeignKey("agent_connections.id", ondelete="CASCADE"), nullable=True
    )
    # queued | claimed | submitted | completed | failed | cancelled | uncertain
    state: Mapped[str] = mapped_column(String(16), default="queued")
    correlation_id: Mapped[str] = mapped_column(String(64))
    claimed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lease_until: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    __table_args__ = (
        Index("ix_dispatch_claim", "state", "connection_id", "created_at"),
        Index("ix_dispatch_run", "run_id"),
    )


class Operation(Timestamps, Base):
    __tablename__ = "operations"
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (Index("ix_operations_state", "state"),)


class ConfigPatch(Timestamps, Base):
    __tablename__ = "config_patches"
    id: Mapped[str] = mapped_column(Id, primary_key=True)
    connection_id: Mapped[str] = mapped_column(Id, ForeignKey("agent_connections.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(Text)
    base_checksum: Mapped[str] = mapped_column(String(64))
    proposed: Mapped[str] = mapped_column(Text)
    diff: Mapped[str] = mapped_column(Text)
    # proposed | applied | unhealthy | rolled_back | rejected
    state: Mapped[str] = mapped_column(String(16), default="proposed")
    backup_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    health: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Meta(Base):
    __tablename__ = "meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


__all__ = ["Base", "text"]
