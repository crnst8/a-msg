"""Device-facing API v1."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import ClientDisconnect

from .. import API_VERSION, SCHEMA_VERSION, __version__, schemas
from ..domain import Principal
from ..domain.threads import FORMATS, TOOLS
from ..errors import ApiError, invalid
from ..providers.base import ProviderError
from ..storage.objects import StagedFile, TooLarge
from .deps import Services, device, services
from .web import client_address

router = APIRouter()
live = APIRouter()
Device = Annotated[Principal, Depends(device)]
App = Annotated[Services, Depends(services)]
ACTIONS = [
    "spaces",
    "messages",
    "fork",
    "archive",
    "delete",
    "cancel",
    "retry",
    "queue",
    "uploads",
    "library",
    "search",
    "automations",
    "connections",
    "config_patches",
]
ERRORS: dict[int | str, dict] = {
    401: {"model": schemas.ErrorEnvelope},
    409: {"model": schemas.ErrorEnvelope},
    422: {"model": schemas.ErrorEnvelope},
    503: {"model": schemas.ErrorEnvelope},
}


@live.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@live.post("/v1/pair", response_model=schemas.PairResult)
def pair(body: schemas.PairRequest, request: Request, app: App) -> schemas.PairResult:
    request.app.state.pair_limiter.check(client_address(request))
    return app.domain.pair(body)


@router.get("/v1/health", response_model=schemas.Health)
def health(_: Device, app: App) -> schemas.Health:
    return app.domain.health()


@router.get("/v1/capabilities", response_model=schemas.Capabilities)
def capabilities(_: Device, app: App) -> schemas.Capabilities:
    domain = app.domain
    connections = domain.list_connections()
    features: dict[str, dict] = {}
    for connection in connections:
        provider = app.runtime.providers.get(connection.kind)
        row = domain.get_connection(connection.id)
        features[connection.id] = connection.capabilities or (provider.features(row) if provider else {})
    return schemas.Capabilities(
        api_version=API_VERSION,
        service_version=__version__,
        service_id=domain.service_id(),
        schema_version=SCHEMA_VERSION,
        actions=ACTIONS,
        formats={**FORMATS, "tools": TOOLS},
        upload_limit=domain.settings.upload_limit,
        provider_features=features,
        deployment={"workspace_roots": [str(p) for p in domain.settings.workspace_roots]},
    )


# Spaces ------------------------------------------------------------------------------------------------
@router.get("/v1/spaces", response_model=list[schemas.SpaceOut])
def list_spaces(_: Device, app: App, include_archived: bool = False) -> list[schemas.SpaceOut]:
    return app.domain.list_spaces(include_archived=include_archived)


@router.post("/v1/spaces", response_model=schemas.SpaceOut, status_code=201, responses=ERRORS)
def create_space(body: schemas.SpaceCreate, who: Device, app: App) -> schemas.SpaceOut:
    return app.domain.create_space(body, who.actor)[0]


@router.patch("/v1/spaces/{space_id}", response_model=schemas.SpaceOut, responses=ERRORS)
def patch_space(space_id: str, body: schemas.SpacePatch, _: Device, app: App) -> schemas.SpaceOut:
    return app.domain.patch_space(space_id, body)


@router.post("/v1/spaces/{space_id}/read", response_model=schemas.SpaceOut)
def read_space(space_id: str, _: Device, app: App) -> schemas.SpaceOut:
    return app.domain.mark_read(space_id)


@router.get("/v1/spaces/{space_id}/messages", response_model=schemas.MessagePage)
def list_messages(
    space_id: str,
    _: Device,
    app: App,
    before: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> schemas.MessagePage:
    return app.domain.list_messages(space_id, before=before, limit=limit)


@router.post("/v1/spaces/{space_id}/messages", response_model=schemas.Receipt, status_code=202, responses=ERRORS)
def post_message(space_id: str, body: schemas.MessageCreate, _: Device, app: App) -> schemas.Receipt:
    return app.domain.accept_message(space_id, body)


@router.post("/v1/spaces/{space_id}/fork", response_model=schemas.ForkResult, status_code=201, responses=ERRORS)
def fork(space_id: str, body: schemas.ForkRequest, who: Device, app: App) -> schemas.ForkResult:
    return app.domain.fork(space_id, body, who.actor)


@router.post("/v1/spaces/{space_id}/archive", response_model=schemas.SpaceOut)
def archive(space_id: str, _: Device, app: App) -> schemas.SpaceOut:
    return app.domain.archive_space(space_id)


@router.post("/v1/spaces/{space_id}/restore", response_model=schemas.SpaceOut)
def restore(space_id: str, _: Device, app: App) -> schemas.SpaceOut:
    return app.domain.archive_space(space_id, restore=True)


@router.post("/v1/spaces/{space_id}/delete", response_model=schemas.OperationOut, status_code=202, responses=ERRORS)
def delete_space(space_id: str, body: schemas.DeletePolicy, who: Device, app: App) -> schemas.OperationOut:
    return app.domain.begin_delete(space_id, body, who.actor)


@router.get("/v1/operations/{operation_id}", response_model=schemas.OperationOut)
def operation(operation_id: str, _: Device, app: App) -> schemas.OperationOut:
    return app.domain.get_operation(operation_id)


@router.post("/v1/operations/{operation_id}/retry", response_model=schemas.OperationOut, status_code=202)
def retry_operation(operation_id: str, _: Device, app: App) -> schemas.OperationOut:
    return app.domain.retry_operation(operation_id)


@router.post("/v1/operations/{operation_id}/dismiss", response_model=schemas.OperationOut)
def dismiss_operation(operation_id: str, _: Device, app: App) -> schemas.OperationOut:
    return app.domain.cancel_operation(operation_id)


# Runs and queue ----------------------------------------------------------------------------------------
@router.post("/v1/runs/{run_id}/cancel", response_model=schemas.RunOut, status_code=202)
async def cancel_run(run_id: str, _: Device, app: App) -> schemas.RunOut:
    return await app.runtime.cancel_run(run_id)


@router.post("/v1/runs/{run_id}/retry", response_model=schemas.RunOut, status_code=202, responses=ERRORS)
def retry_run(run_id: str, _: Device, app: App) -> schemas.RunOut:
    return app.domain.retry_run(run_id)


@router.get("/v1/queued-messages/{message_id}", response_model=schemas.MessageOut, responses=ERRORS)
def queued_get(message_id: str, _: Device, app: App) -> schemas.MessageOut:
    return app.domain.get_queued(message_id)


@router.patch("/v1/queued-messages/{message_id}", response_model=schemas.MessageOut, responses=ERRORS)
def queued_patch(message_id: str, body: schemas.QueuedMessagePatch, _: Device, app: App) -> schemas.MessageOut:
    return app.domain.edit_queued(message_id, body)


@router.delete("/v1/queued-messages/{message_id}", status_code=204, responses=ERRORS)
def queued_delete(message_id: str, _: Device, app: App) -> Response:
    app.domain.remove_queued(message_id)
    return Response(status_code=204)


# Events ------------------------------------------------------------------------------------------------
@router.get("/v1/snapshot", response_model=schemas.Snapshot)
def snapshot(_: Device, app: App) -> schemas.Snapshot:
    return app.domain.snapshot()


@router.get("/v1/events", responses={410: {"model": schemas.ErrorEnvelope}})
async def events(request: Request, who: Device, app: App, after: int = 0) -> StreamingResponse:
    domain = app.domain
    await run_in_threadpool(domain.check_cursor, after)
    loop = asyncio.get_running_loop()
    wake = asyncio.Event()
    token = request.headers.get("authorization", "").partition(" ")[2].strip()

    def on_commit() -> None:
        if not loop.is_closed():
            loop.call_soon_threadsafe(wake.set)

    unsubscribe = domain.notifier.subscribe(on_commit)
    stopping: asyncio.Event = request.app.state.stopping

    async def stream():  # type: ignore[no-untyped-def]
        cursor = after
        try:
            yield f"event: ready\ndata: {json.dumps({'cursor': cursor})}\n\n"
            while not stopping.is_set():
                # Cleared before reading: a commit after the read must still end the wait below.
                wake.clear()
                rows = await run_in_threadpool(domain.events_after, cursor)
                for row in rows:
                    cursor = row.seq
                    data = json.dumps(
                        {
                            "seq": row.seq,
                            "schema_version": row.schema_version,
                            "type": row.type,
                            "entity_id": row.entity_id,
                            "payload": row.payload,
                            "created_at": row.created_at,
                        }
                    )
                    yield f"id: {row.seq}\nevent: {row.type}\ndata: {data}\n\n"
                if rows:
                    continue
                try:
                    await asyncio.wait_for(wake.wait(), timeout=domain.settings.heartbeat_seconds)
                except TimeoutError:
                    if await run_in_threadpool(domain.authenticate, token) is None:
                        return
                    yield ": heartbeat\n\n"
        finally:
            unsubscribe()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# Files -------------------------------------------------------------------------------------------------
class _Multipart:
    """Streams the single ``file`` part to a staged file; the limit applies while bytes arrive."""

    def __init__(self, stage) -> None:  # type: ignore[no-untyped-def]
        self.stage = stage
        self.staged: StagedFile | None = None
        self.name: str | None = None
        self.mime: str | None = None
        self._header = b""
        self._value = b""
        self._headers: dict[bytes, bytes] = {}
        self._active = False
        self.complete = False

    def callbacks(self) -> dict:
        return {
            "on_part_begin": self._begin,
            "on_part_data": self._data,
            "on_part_end": self._end,
            "on_header_field": self._field,
            "on_header_value": self._val,
            "on_header_end": self._header_end,
            "on_headers_finished": self._headers_done,
        }

    def _begin(self) -> None:
        self._headers = {}
        self._active = False

    def _field(self, data: bytes, start: int, end: int) -> None:
        self._header += data[start:end]

    def _val(self, data: bytes, start: int, end: int) -> None:
        self._value += data[start:end]

    def _header_end(self) -> None:
        self._headers[self._header.lower()] = self._value
        self._header = self._value = b""

    def _headers_done(self) -> None:
        _, options = parse_options_header(self._headers.get(b"content-disposition", b""))
        if options.get(b"name") == b"file" and b"filename" in options and self.staged is None:
            self.name = options[b"filename"].decode("utf-8", "replace")
            mime = self._headers.get(b"content-type")
            self.mime = mime.decode("latin-1") if mime else None
            self.staged = self.stage()
            self._active = True

    def _data(self, data: bytes, start: int, end: int) -> None:
        if self._active and self.staged is not None:
            self.staged.write(data[start:end])

    def _end(self) -> None:
        if self._active:
            self.complete = True
            self._active = False


async def _receive_upload(request: Request, limit: int, stage) -> _Multipart:  # type: ignore[no-untyped-def]
    content_type, options = parse_options_header(request.headers.get("content-type", ""))
    boundary = options.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary:
        raise invalid("multipart_required", "Send the file as multipart/form-data.")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit + 1024 * 1024:
        raise ApiError(413, "payload_too_large", "This file is over the upload limit.", details={"limit": limit})
    state = _Multipart(stage)
    parser = MultipartParser(boundary, state.callbacks())
    try:
        async for chunk in request.stream():
            parser.write(chunk)
        parser.finalize()
    except TooLarge as error:
        raise ApiError(
            413, "payload_too_large", "This file is over the upload limit.", details={"limit": limit}
        ) from error
    except ClientDisconnect:
        if state.staged is not None:
            state.staged.abort()
        raise ApiError(400, "upload_cancelled", "The upload was cancelled.") from None
    except Exception:
        if state.staged is not None:
            state.staged.abort()
        raise
    if state.staged is None or not state.complete:
        if state.staged is not None:
            state.staged.abort()
        raise invalid("file_missing", "No complete file was received.")
    return state


@router.post("/v1/uploads", response_model=schemas.ArtifactRef, status_code=201, responses=ERRORS)
async def upload(request: Request, _: Device, app: App) -> schemas.ArtifactRef:
    limit = app.domain.settings.upload_limit
    state = await _receive_upload(request, limit, lambda: app.domain.store.stage(limit))
    try:
        return await run_in_threadpool(app.domain.register_upload, state.staged, state.name, state.mime)
    except ApiError:
        state.staged.abort()
        raise


@router.get("/v1/artifacts", response_model=schemas.ArtifactPage)
def list_artifacts(
    _: Device,
    app: App,
    q: str | None = None,
    type: str | None = None,
    space_id: str | None = None,
    include_hidden: bool = False,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> schemas.ArtifactPage:
    return app.domain.list_artifacts(
        q=q, type_filter=type, space_id=space_id, include_hidden=include_hidden, offset=offset, limit=limit
    )


@router.get("/v1/artifacts/{artifact_id}", response_model=schemas.ArtifactOut)
def get_artifact(artifact_id: str, _: Device, app: App) -> schemas.ArtifactOut:
    return app.domain.get_artifact(artifact_id)


RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


def artifact_response(
    app: Services, artifact_id: str, version: int | None, range_header: str | None
) -> StreamingResponse:
    artifact, row = app.domain.artifact_blob(artifact_id, version)
    size = row.size
    if not app.domain.store.exists(row.storage_key):
        raise ApiError(410, "file_unavailable", "The stored bytes for this file are missing.")
    start, end, status = 0, size - 1, 200
    if range_header:
        match = RANGE.match(range_header.strip())
        if match is None or (not match[1] and not match[2]):
            raise ApiError(416, "range_invalid", "That byte range is not valid.")
        if match[1]:
            start = int(match[1])
            end = min(int(match[2]), size - 1) if match[2] else size - 1
        else:
            start = max(size - int(match[2]), 0)
        if start > end or start >= size:
            raise ApiError(416, "range_invalid", "That byte range is not valid.")
        status = 206
    # Bytes are always served as an attachment and sandboxed so nothing runs in the app origin.
    safe_ascii = artifact.display_name.encode("ascii", "replace").decode().replace('"', "")
    from urllib.parse import quote

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Disposition": (
            f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{quote(artifact.display_name)}"
        ),
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "X-Checksum-Sha256": row.checksum,
        "Cache-Control": "private, no-store",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        app.domain.store.read_range(row.storage_key, start, end),
        status_code=status,
        media_type=artifact.mime,
        headers=headers,
    )


@router.get("/v1/artifacts/{artifact_id}/content")
def artifact_content(
    artifact_id: str,
    _: Device,
    app: App,
    version: int | None = None,
    range_header: Annotated[str | None, Header(alias="range")] = None,
) -> StreamingResponse:
    return artifact_response(app, artifact_id, version, range_header)


@router.post("/v1/artifacts/{artifact_id}/hide", response_model=schemas.ArtifactOut)
def hide_artifact(artifact_id: str, _: Device, app: App) -> schemas.ArtifactOut:
    return app.domain.set_hidden(artifact_id, True)


@router.post("/v1/artifacts/{artifact_id}/unhide", response_model=schemas.ArtifactOut)
def unhide_artifact(artifact_id: str, _: Device, app: App) -> schemas.ArtifactOut:
    return app.domain.set_hidden(artifact_id, False)


@router.delete("/v1/artifacts/{artifact_id}", response_model=schemas.ArtifactOut, responses=ERRORS)
def delete_artifact(artifact_id: str, _: Device, app: App, confirm: bool = False) -> schemas.ArtifactOut:
    return app.domain.delete_artifact(artifact_id, confirm=confirm)


# Search ------------------------------------------------------------------------------------------------
@router.get("/v1/search", response_model=schemas.SearchResult)
def search(
    q: Annotated[str, Query(min_length=1, max_length=200)],
    _: Device,
    app: App,
    space_id: str | None = None,
    type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=40)] = 20,
) -> schemas.SearchResult:
    return app.domain.search(q, space_id=space_id, type_filter=type, limit=limit)


# Connections and automations ---------------------------------------------------------------------------
@router.get("/v1/connections", response_model=list[schemas.ConnectionOut])
def list_connections(_: Device, app: App) -> list[schemas.ConnectionOut]:
    return app.domain.list_connections()


@router.post("/v1/connections", response_model=schemas.ConnectionOut, status_code=201, responses=ERRORS)
def create_connection(body: schemas.ConnectionCreate, _: Device, app: App) -> schemas.ConnectionOut:
    return app.domain.create_connection(body)


@router.post("/v1/connections/{connection_id}/test", response_model=schemas.ConnectionTest)
async def test_connection(connection_id: str, _: Device, app: App) -> schemas.ConnectionTest:
    domain = app.domain
    connection = await run_in_threadpool(domain.get_connection, connection_id)
    provider = app.runtime.providers.get(connection.kind)
    if provider is None:
        checks = [{"name": "provider", "ok": False, "detail": "This kind of connection is not available."}]
        return schemas.ConnectionTest(ok=False, state="error", checks=checks, capabilities={})
    checks = await asyncio.wait_for(provider.test(connection), timeout=45)
    if not provider.in_process and all(check["ok"] for check in checks):
        try:
            reply = await app.runtime.command(connection, "probe", {}, timeout=15)
            checks.append({"name": "routing", "ok": True, "detail": "The connector answered through its work queue"})
            checks.append({"name": "formats", "ok": bool(reply.get("formats")), "detail": "Reply formats reported"})
            checks.extend(reply.get("checks") or [])
        except ProviderError as error:
            checks.append({"name": "routing", "ok": False, "detail": error.message})
    ok = all(check["ok"] for check in checks)
    features = provider.features(connection)
    state = "connected" if ok else "error"
    await run_in_threadpool(domain.update_connection_state, connection_id, state, capabilities=features, checked=True)
    return schemas.ConnectionTest(ok=ok, state=state, checks=checks, capabilities=features)


@router.get("/v1/connections/{connection_id}/config", response_model=schemas.ConfigFile, responses=ERRORS)
def read_config(connection_id: str, _: Device, app: App) -> schemas.ConfigFile:
    return app.domain.read_config(connection_id)


@router.post(
    "/v1/connections/{connection_id}/config-patches",
    response_model=schemas.ConfigPatchOut,
    status_code=201,
    responses=ERRORS,
)
def propose_patch(connection_id: str, body: schemas.ConfigPatchCreate, _: Device, app: App) -> schemas.ConfigPatchOut:
    return app.domain.propose_patch(connection_id, body)


@router.post("/v1/config-patches/{patch_id}/apply", response_model=schemas.ConfigPatchOut, responses=ERRORS)
def apply_patch(patch_id: str, _: Device, app: App) -> schemas.ConfigPatchOut:
    return app.domain.apply_patch(patch_id)


@router.post("/v1/config-patches/{patch_id}/rollback", response_model=schemas.ConfigPatchOut, responses=ERRORS)
def rollback_patch(patch_id: str, _: Device, app: App) -> schemas.ConfigPatchOut:
    return app.domain.rollback_patch(patch_id)


@router.get("/v1/automations", response_model=list[schemas.AutomationSummary])
def list_automations(_: Device, app: App) -> list[schemas.AutomationSummary]:
    return app.domain.list_automations()


@router.post("/v1/automations/{link_id}/actions", response_model=schemas.AutomationSummary, responses=ERRORS)
async def automation_action(
    link_id: str, body: schemas.AutomationAction, _: Device, app: App
) -> schemas.AutomationSummary:
    return await app.runtime.automation_action(link_id, body)


@router.get("/v1/devices", response_model=list[schemas.DeviceOut])
def list_devices(who: Device, app: App) -> list[schemas.DeviceOut]:
    return app.domain.list_devices(who.id)


@router.delete("/v1/devices/{device_id}", status_code=204)
def revoke_device(device_id: str, _: Device, app: App) -> Response:
    app.domain.revoke(device_id)
    return Response(status_code=204)
