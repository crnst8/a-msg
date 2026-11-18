"""Connector-facing routes. Everything here is scoped to the authenticated connection and its runs."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from .. import schemas
from ..db.models import DispatchJob, Run
from ..domain import Domain, Principal
from ..domain.base import ACTIVE_RUN_STATES, now
from ..errors import ApiError, invalid, missing
from .deps import Services, connector, services
from .routes import _receive_upload

router = APIRouter(prefix="/v1/connector")
Connector = Annotated[Principal, Depends(connector)]
App = Annotated[Services, Depends(services)]


def _next_job(app: Services, connection_id: str) -> schemas.ConnectorJob | None:
    domain = app.domain
    waiting = (
        select(DispatchJob)
        .where(
            DispatchJob.connection_id == connection_id,
            DispatchJob.kind.in_(("cancel", "command")),
            DispatchJob.state == "queued",
        )
        .order_by(DispatchJob.created_at)
        .limit(1)
    )
    with domain.db.read() as session:
        idle = session.scalars(waiting).first() is None
    if idle:
        return _claim_message(domain, connection_id)
    with domain.db.write() as session:
        job = session.scalars(waiting).first()
        if job is not None:
            job.state = "claimed"
            job.claimed_at = now()
            job.updated_at = now()
            return schemas.ConnectorJob(
                id=job.id,
                kind=job.kind,
                correlation_id=job.correlation_id,
                run_id=job.run_id,
                space_id=job.space_id,
                message_id=job.message_id,
                payload=job.payload or {},
            )
    return _claim_message(domain, connection_id)


def _claim_message(domain: Domain, connection_id: str) -> schemas.ConnectorJob | None:
    claimed = domain.claim_message_job([connection_id])
    if claimed is None:
        return None
    return schemas.ConnectorJob(
        id=claimed["job_id"],
        kind="message",
        correlation_id=claimed["correlation_id"],
        run_id=claimed["run_id"],
        space_id=claimed["space_id"],
        message_id=claimed["reply_message_id"],
        payload={"context": claimed["context"]},
    )


@router.get("/dispatch", response_model=schemas.ConnectorJob | None)
async def dispatch(request: Request, who: Connector, app: App, wait: float = 20.0) -> schemas.ConnectorJob | None:
    """Long poll. Reconnecting never re-delivers a job that was already claimed."""
    assert who.connection_id
    domain = app.domain
    await run_in_threadpool(domain.update_connection_state, who.connection_id, "connected", seen=True)
    loop = asyncio.get_running_loop()
    wake = asyncio.Event()

    def on_commit() -> None:
        if not loop.is_closed():
            loop.call_soon_threadsafe(wake.set)

    unsubscribe = domain.notifier.subscribe(on_commit)
    stopping: asyncio.Event = request.app.state.stopping
    deadline = loop.time() + min(max(wait, 0.0), 30.0)

    async def abandoned() -> bool:
        return stopping.is_set() or await request.is_disconnected()

    try:
        while True:
            # A poll whose client left must not claim work: nobody would receive it.
            if await abandoned():
                return None
            wake.clear()
            job = await run_in_threadpool(_next_job, app, who.connection_id)
            if job is not None:
                return job
            # Asleep until a commit, the deadline, or a check that the client is still connected.
            while not wake.is_set():
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return None
                try:
                    await asyncio.wait_for(wake.wait(), timeout=min(remaining, 1.0))
                except TimeoutError:
                    if await abandoned():
                        return None
    finally:
        unsubscribe()


def _bound_run(app: Services, who: Principal, run_id: str) -> Run:
    """A tool or result can only touch a run dispatched to this connector, regardless of what it claims."""
    with app.domain.db.read() as session:
        run = session.get(Run, run_id)
        job = session.scalar(select(DispatchJob).where(DispatchJob.run_id == run_id, DispatchJob.kind == "message"))
        if run is None or job is None or job.connection_id != who.connection_id:
            raise ApiError(403, "forbidden", "This run does not belong to this connector.")
        return run


def _active_run(app: Services, who: Principal, run_id: str) -> Run:
    run = _bound_run(app, who, run_id)
    if run.state not in ACTIVE_RUN_STATES:
        raise ApiError(403, "run_finished", "This run has finished.")
    return run


@router.post("/dispatch/{job_id}/ack", status_code=204)
def ack(job_id: str, body: schemas.ConnectorAck, who: Connector, app: App) -> None:
    with app.domain.db.read() as session:
        job = session.get(DispatchJob, job_id)
        if job is None or job.connection_id != who.connection_id:
            raise missing("Dispatch job")
    app.domain.acknowledge_dispatch(job_id, body.provider_run_id)


@router.post("/dispatch/{job_id}/result", status_code=204)
def command_result(job_id: str, body: schemas.ConnectorCommandResult, who: Connector, app: App) -> None:
    with app.domain.db.write() as session:
        job = session.get(DispatchJob, job_id)
        if job is None or job.connection_id != who.connection_id:
            raise missing("Dispatch job")
        job.state = "completed" if body.ok else "failed"
        job.result = body.result if body.ok else {"error": body.error}
        job.updated_at = now()


@router.get("/runs/{correlation_id}")
def reconcile(correlation_id: str, who: Connector, app: App) -> dict[str, Any]:
    """Lets a restarted adapter ask what became of a run it was handling."""
    run = _bound_run(app, who, correlation_id)
    return {"run_id": run.id, "state": run.state, "provider_run_id": run.provider_run_id}


@router.post("/runs/{run_id}/delta", status_code=204)
def delta(run_id: str, body: schemas.ConnectorDelta, who: Connector, app: App) -> None:
    _active_run(app, who, run_id)
    app.domain.append_delta(run_id, body.append)
    with app.domain.db.write() as session:
        app.domain.extend_lease(session, run_id)


@router.put("/runs/{run_id}/body", status_code=204)
def replace_body(run_id: str, body: schemas.ConnectorComplete, who: Connector, app: App) -> None:
    _active_run(app, who, run_id)
    if body.markdown is not None:
        app.domain.replace_body(run_id, body.markdown)
    with app.domain.db.write() as session:
        app.domain.extend_lease(session, run_id)


@router.post("/runs/{run_id}/activity", status_code=204)
def activity(run_id: str, body: schemas.ConnectorActivity, who: Connector, app: App) -> None:
    _active_run(app, who, run_id)
    app.domain.set_activity(run_id, body.text)
    with app.domain.db.write() as session:
        app.domain.extend_lease(session, run_id)


@router.post("/runs/{run_id}/complete", status_code=204)
def complete(run_id: str, body: schemas.ConnectorComplete, who: Connector, app: App) -> None:
    _bound_run(app, who, run_id)
    app.domain.complete_run(run_id, body.markdown, body.artifact_ids)


@router.post("/runs/{run_id}/fail", status_code=204)
def fail(run_id: str, body: schemas.ConnectorFail, who: Connector, app: App) -> None:
    _bound_run(app, who, run_id)
    app.domain.fail_run(run_id, body.code, body.message)


@router.post("/runs/{run_id}/cancelled", status_code=204)
def cancelled(run_id: str, who: Connector, app: App) -> None:
    _bound_run(app, who, run_id)
    app.domain.confirm_cancelled(run_id)


@router.post("/runs/{run_id}/session", status_code=204)
def set_session(run_id: str, body: schemas.ConnectorAck, who: Connector, app: App) -> None:
    run = _bound_run(app, who, run_id)
    if body.provider_run_id:
        app.domain.set_provider_session(run.space_id, body.provider_run_id)


@router.post("/messages", response_model=schemas.MessageOut, status_code=201)
def post_message(body: schemas.ConnectorPost, who: Connector, app: App) -> schemas.MessageOut:
    """Delivery with no run behind it, such as a scheduled job's result."""
    assert who.connection_id
    domain = app.domain
    with domain.db.read() as session:
        domain.space_or_404(session, body.space_id)
    connection = domain.get_connection(who.connection_id)
    meta = {"automation_job_id": body.automation_job_id} if body.automation_job_id else {}
    return domain.post_agent_message(
        body.space_id,
        body.markdown,
        author=connection.name,
        meta=meta,
        artifact_ids=body.artifact_ids,
        dedupe_key=body.correlation_id,
    )


@router.post("/capabilities", status_code=204)
def report_capabilities(body: dict[str, Any], who: Connector, app: App) -> None:
    assert who.connection_id
    roots = body.get("allowed_roots")
    if isinstance(roots, list) and app.domain.get_connection(who.connection_id).kind == "bridge":
        # A runner decides which of its own folders are open; the app lists them.
        app.domain.set_connection_config(who.connection_id, allowed_roots=[str(root) for root in roots])
    app.domain.update_connection_state(who.connection_id, "connected", capabilities=body, seen=True)


@router.post("/uploads", response_model=schemas.ArtifactRef, status_code=201)
async def connector_upload(
    request: Request,
    who: Connector,
    app: App,
    run_id: str | None = None,
    space_id: str | None = None,
    attach: bool = False,
) -> schemas.ArtifactRef:
    """Agent files. Bytes bound to a run land on its reply; bytes for a space cover scheduled delivery."""
    domain = app.domain
    message_id = None
    if run_id is not None:
        run = await run_in_threadpool(_active_run, app, who, run_id)
        space_id, message_id = run.space_id, run.reply_message_id
    elif space_id is not None:
        await run_in_threadpool(domain.get_space, space_id)
    else:
        raise invalid("space_required", "An upload needs a run or a space.")
    limit = domain.settings.upload_limit
    state = await _receive_upload(request, limit, lambda: domain.store.stage(limit))
    ref = await run_in_threadpool(
        domain.register_upload,
        state.staged,
        state.name,
        state.mime,
        source="agent",
        space_id=space_id,
        message_id=message_id,
    )
    if attach and message_id:
        await run_in_threadpool(domain.attach_to_message, message_id, ref.id)
    return ref


@router.get("/artifacts/{artifact_id}/content")
def connector_artifact(artifact_id: str, who: Connector, app: App, run_id: str):  # type: ignore[no-untyped-def]
    """Attachment bytes for a run, readable only while that run references them."""
    from ..db.models import MessageArtifact
    from .routes import artifact_response

    run = _active_run(app, who, run_id)
    with app.domain.db.read() as session:
        linked = session.get(MessageArtifact, (run.request_message_id, artifact_id))
    if linked is None:
        raise ApiError(403, "forbidden", "This file is not attached to the run.")
    return artifact_response(app, artifact_id, None, None)


@router.post("/automations/sync", status_code=204)
def automations_sync(body: schemas.ConnectorAutomationSync, who: Connector, app: App) -> None:
    assert who.connection_id
    app.domain.sync_automations(who.connection_id, body.jobs)


@router.post("/tools/{name}")
async def tool(name: str, body: schemas.ToolCall, who: Connector, app: App) -> dict[str, Any]:
    """Typed tools. The caller's run decides scope; arguments cannot name another run's context."""
    domain = app.domain
    run = await run_in_threadpool(_active_run, app, who, body.run_id)
    args = body.arguments

    def text_arg(key: str, limit: int = 200) -> str:
        value = args.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise invalid("tool_argument", f"{key} must be a non-empty string up to {limit} characters.")
        return value

    def icon_arg() -> schemas.Icon | None:
        value = args.get("icon")
        if value is None:
            return None
        if isinstance(value, str):
            return schemas.Icon(kind="catalog", value=value[:64])
        return schemas.Icon.model_validate(value)

    def revision(space_id: str) -> int:
        return domain.get_space(space_id).revision

    def call() -> dict[str, Any]:
        if name == "list_spaces":
            return {
                "spaces": [
                    {"id": s.id, "title": s.title, "kind": s.kind, "icon": s.icon.value} for s in domain.list_spaces()
                ]
            }
        if name == "create_space":
            key = f"tool:{body.run_id}:{text_arg('title')}"
            space, duplicate = domain.create_space(
                schemas.SpaceCreate(
                    title=text_arg("title"), icon=icon_arg() or schemas.Icon(), idempotency_key=key[:128]
                ),
                who.actor,
            )
            return {"space_id": space.id, "title": space.title, "duplicate": duplicate}
        if name == "rename_space":
            space_id = text_arg("space_id", 36)
            patch = schemas.SpacePatch(expected_revision=revision(space_id), title=text_arg("title"))
            return {"space_id": domain.patch_space(space_id, patch).id}
        if name == "set_space_icon":
            space_id = text_arg("space_id", 36)
            icon = icon_arg()
            if icon is None:
                raise invalid("tool_argument", "icon is required.")
            return {
                "space_id": domain.patch_space(
                    space_id, schemas.SpacePatch(expected_revision=revision(space_id), icon=icon)
                ).id
            }
        if name == "fork_conversation":
            ids = args.get("source_message_ids") or [run.request_message_id]
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise invalid("tool_argument", "source_message_ids must be a list of IDs.")
            suffix = args.get("idempotency_key") or ",".join(ids)
            result = domain.fork(
                run.space_id,
                schemas.ForkRequest(
                    source_message_ids=ids,
                    title=args.get("title"),
                    icon=icon_arg(),
                    summary=args.get("summary"),
                    idempotency_key=f"tool:{run.id}:{suffix}"[:128],
                ),
                who.actor,
            )
            return {"space_id": result.space.id, "title": result.space.title, "duplicate": result.duplicate}
        if name == "register_artifact":
            ref = domain.register_path(text_arg("path", 4096), space_id=run.space_id, message_id=run.reply_message_id)
            domain.attach_to_message(run.reply_message_id, ref.id)
            return {"artifact_id": ref.id, "name": ref.display_name}
        if name == "read_thread_context":
            return domain.read_thread_context(run.space_id, int(args.get("offset", 0)), int(args.get("limit", 20)))
        if name == "list_automations":
            return {"automations": [a.model_dump() for a in domain.list_automations()]}
        raise ApiError(404, "unknown_tool", "That tool does not exist.")

    if name == "update_delivery_target":
        link_id = args.get("automation_id")
        space_id = args.get("space_id")
        if not isinstance(link_id, str) or not isinstance(space_id, str):
            raise invalid("tool_argument", "automation_id and space_id are required.")
        result = await app.runtime.automation_action(
            link_id, schemas.AutomationAction(action="redirect", destination_space_id=space_id)
        )
        return {"automation_id": result.id, "space_id": result.space_id}
    return await run_in_threadpool(call)
