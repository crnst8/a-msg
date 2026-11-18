"""HTTP contracts. OpenAPI is exported from these models and TypeScript types are generated from it."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SpaceKind = Literal["general", "thread", "code"]
IconKind = Literal["catalog", "emoji"]
Role = Literal["user", "agent", "context", "event"]
MessageStatus = Literal[
    "accepted",
    "queued_server",
    "running",
    "cancel_requested",
    "completed",
    "failed",
    "cancelled",
    "delivery_uncertain",
]
RunState = Literal["queued", "running", "cancel_requested", "completed", "failed", "cancelled", "delivery_uncertain"]


class Model(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorBody(Model):
    code: str
    message: str
    retryable: bool = False
    request_id: str | None = None
    details: dict[str, Any] = {}


class ErrorEnvelope(Model):
    error: ErrorBody


class Icon(Model):
    kind: IconKind = "catalog"
    value: str = Field(default="thread", min_length=1, max_length=64)


class AutomationSummary(Model):
    id: str
    name: str
    schedule: str
    timezone: str
    next_run_at: str | None
    last_run_at: str | None
    last_outcome: str | None
    state: str
    stale: bool = False
    space_id: str | None = None
    connection_id: str
    provider_job_id: str
    last_synced_at: str | None = None
    revision: int = 1


Access = Literal["read", "workspace", "full"]


class SessionInfo(Model):
    connection_id: str
    working_directory: str | None = None
    access: Access | None = None
    provider_session_id: str | None = None
    state: str = "active"


class SpaceOut(Model):
    id: str
    title: str
    icon: Icon
    kind: SpaceKind
    ordinal: int
    agent_connection_id: str | None
    archived_at: str | None
    unread: bool
    created_at: str
    updated_at: str
    revision: int
    session: SessionInfo | None = None
    automation_count: int = 0
    pending_operation_id: str | None = None


class SpaceCreate(Model):
    title: str = Field(min_length=1, max_length=200)
    icon: Icon = Icon()
    kind: Literal["thread", "code"] = "thread"
    agent_connection_id: str | None = None
    working_directory: str | None = None
    access: Access | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class SpacePatch(Model):
    expected_revision: int
    title: str | None = Field(default=None, min_length=1, max_length=200)
    icon: Icon | None = None
    ordinal: int | None = Field(default=None, ge=0)
    access: Access | None = None


class ArtifactRef(Model):
    id: str
    display_name: str
    mime: str
    size: int
    checksum: str
    version: int
    state: str
    hidden: bool = False
    previewable: bool = False


class MessageLink(Model):
    direction: Literal["continued_in", "from"]
    space_id: str
    title: str
    available: bool = True


class MessageOut(Model):
    id: str
    seq: int
    space_id: str
    role: Role
    markdown: str
    status: MessageStatus
    client_message_id: str | None
    run_id: str | None
    author: str | None
    activity: str | None
    attempt: int = 1
    error_code: str | None = None
    error_message: str | None = None
    revision: int
    meta: dict[str, Any] = {}
    artifacts: list[ArtifactRef] = []
    links: list[MessageLink] = []
    created_at: str
    updated_at: str


class MessagePage(Model):
    items: list[MessageOut]
    next_before: int | None


class MessageCreate(Model):
    client_message_id: str = Field(min_length=8, max_length=64)
    markdown: str = Field(default="", max_length=200_000)
    artifact_ids: list[str] = Field(default=[], max_length=32)


class RunOut(Model):
    id: str
    space_id: str
    request_message_id: str | None
    reply_message_id: str | None
    provider_run_id: str | None
    state: RunState
    attempt_of: str | None
    attempt: int
    error_code: str | None
    error_message: str | None
    accepted_at: str
    started_at: str | None
    finished_at: str | None
    revision: int


class Receipt(Model):
    message: MessageOut
    run: RunOut
    duplicate: bool = False


class QueuedMessagePatch(Model):
    expected_revision: int
    markdown: str = Field(min_length=1, max_length=200_000)


class ForkRequest(Model):
    source_message_ids: list[str] = Field(min_length=1, max_length=500)
    title: str | None = Field(default=None, max_length=200)
    icon: Icon | None = None
    summary: str | None = Field(default=None, max_length=4000)
    agent_connection_id: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=128)


class ForkResult(Model):
    space: SpaceOut
    duplicate: bool = False


class DeletePolicy(Model):
    policy: Literal["delete", "keep_and_move", "stop_and_delete"] = "delete"
    expected_revision: int
    idempotency_key: str = Field(min_length=8, max_length=128)


class OperationOut(Model):
    id: str
    kind: str
    state: str
    entity_id: str | None
    progress: dict[str, Any]
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str


class ConnectionOut(Model):
    id: str
    name: str
    kind: str
    state: str
    config: dict[str, Any]
    capabilities: dict[str, Any]
    last_checked_at: str | None
    last_seen_at: str | None
    has_secret: bool = False
    revision: int
    development_only: bool = False


class ConnectionCreate(Model):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["codex", "claude", "bridge", "hermes"]
    config: dict[str, Any] = {}
    secrets: dict[str, str] = {}


class ConnectionTest(Model):
    ok: bool
    state: str
    checks: list[dict[str, Any]]
    capabilities: dict[str, Any]


class ArtifactOut(ArtifactRef):
    origin_space_id: str | None
    origin_space_title: str | None = None
    origin_message_id: str | None
    source: str
    created_at: str
    updated_at: str
    versions: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []


class ArtifactPage(Model):
    items: list[ArtifactOut]
    next_offset: int | None


class ArtifactDelete(Model):
    confirm: bool = False


class SearchHit(Model):
    type: Literal["message", "artifact", "space"]
    id: str
    space_id: str | None
    title: str
    excerpt: str
    created_at: str | None = None


class SearchResult(Model):
    query: str
    items: list[SearchHit]


class AutomationAction(Model):
    action: Literal["pause", "resume", "run_now", "redirect", "remove"]
    expected_revision: int | None = None
    destination_space_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class ActiveRun(Model):
    id: str
    space_id: str
    state: RunState
    reply_message_id: str | None


class Snapshot(Model):
    cursor: int
    service_id: str
    spaces: list[SpaceOut]
    connections: list[ConnectionOut]
    automations: list[AutomationSummary]
    active_runs: list[ActiveRun]
    queued_messages: list[MessageOut]


class Capabilities(Model):
    api_version: str
    service_version: str
    service_id: str
    schema_version: int
    actions: list[str]
    formats: dict[str, Any]
    upload_limit: int
    provider_features: dict[str, dict[str, Any]]
    deployment: dict[str, Any]


class Health(Model):
    ok: bool
    version: str
    database: dict[str, Any]
    connections: list[dict[str, Any]]
    queue: dict[str, int]


class PairRequest(Model):
    code: str = Field(min_length=6, max_length=64)
    device_name: str = Field(default="Mac", min_length=1, max_length=120)


class PairResult(Model):
    token: str
    device_id: str
    service_id: str
    service_version: str


class WebSession(Model):
    configured: bool
    signed_in: bool
    service_id: str | None = None
    device_id: str | None = None


class WebSignIn(Model):
    password: str = Field(min_length=1, max_length=1024)
    code: str = Field(min_length=6, max_length=12)
    device_name: str = Field(default="Browser", min_length=1, max_length=120)


class WebSignInResult(Model):
    service_id: str
    device_id: str


class DeviceOut(Model):
    id: str
    name: str
    created_at: str
    last_seen_at: str | None
    revoked_at: str | None
    current: bool = False


class ConfigPatchCreate(Model):
    path: str
    proposed: str
    base_checksum: str


class ConfigPatchOut(Model):
    id: str
    connection_id: str
    path: str
    base_checksum: str
    diff: str
    state: str
    health: dict[str, Any] | None
    created_at: str
    updated_at: str


class ConfigFile(Model):
    path: str
    content: str
    checksum: str


# Connector contracts
class ConnectorJob(Model):
    id: str
    kind: str
    correlation_id: str
    run_id: str | None
    space_id: str | None
    message_id: str | None
    payload: dict[str, Any]


class ConnectorAck(Model):
    provider_run_id: str | None = None


class ConnectorDelta(Model):
    append: str = Field(default="", max_length=64_000)


class ConnectorActivity(Model):
    text: str | None = Field(default=None, max_length=300)


class ConnectorComplete(Model):
    markdown: str | None = Field(default=None, max_length=2_000_000)
    artifact_ids: list[str] = []


class ConnectorFail(Model):
    code: str = Field(default="provider_error", max_length=64)
    message: str = Field(default="The agent reported a failure.", max_length=2000)


class ConnectorPost(Model):
    """Out-of-band delivery, such as a scheduled run posting into its destination."""

    space_id: str
    markdown: str = Field(max_length=2_000_000)
    automation_job_id: str | None = None
    artifact_ids: list[str] = []
    correlation_id: str | None = Field(default=None, max_length=64)


class ConnectorCommandResult(Model):
    ok: bool
    result: dict[str, Any] = {}
    error: str | None = None


class ConnectorJobReport(Model):
    provider_job_id: str
    name: str
    schedule: str = ""
    timezone: str = "UTC"
    next_run_at: str | None = None
    last_run_at: str | None = None
    last_outcome: str | None = None
    state: Literal["active", "paused"] = "active"
    destination: str | None = None


class ConnectorAutomationSync(Model):
    jobs: list[ConnectorJobReport]


class ToolCall(Model):
    run_id: str
    arguments: dict[str, Any] = {}
