"""Background work: dispatch, cancellation routing, retention and durable operations."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select

from .. import schemas
from ..db.models import AgentConnection, DispatchJob
from ..domain import Domain
from ..domain.base import later, new_id, now
from ..errors import ApiError, unavailable
from ..providers import Provider, ProviderRegistry, RunSink
from ..providers.base import ProviderError

log = logging.getLogger("amsg.worker")
CONNECTOR_SILENCE_SECONDS = 90
COMMAND_TIMEOUT = 30.0


class Runtime:
    def __init__(self, domain: Domain, providers: ProviderRegistry) -> None:
        self.domain = domain
        self.providers = providers
        self._wake = asyncio.Event()
        self._stopping = asyncio.Event()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._running: dict[str, asyncio.Task[Any]] = {}
        self._loops: list[asyncio.Task[Any]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._unsubscribe: Any = None

    def _on_commit(self) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._wake.set)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._unsubscribe = self.domain.notifier.subscribe(self._on_commit)
        await asyncio.to_thread(self.domain.reconcile_after_restart, self.providers.in_process_kinds())
        self._loops = [
            asyncio.create_task(self._dispatch_loop(), name="dispatch"),
            asyncio.create_task(self._maintenance_loop(), name="maintenance"),
            asyncio.create_task(self._operations_loop(), name="operations"),
        ]

    async def stop(self) -> None:
        """Stop claiming, let writes finish, record what could not complete."""
        self._stopping.set()
        self._wake.set()
        for task in self._loops:
            task.cancel()
        await asyncio.gather(*self._loops, return_exceptions=True)
        for task in list(self._running.values()):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._unsubscribe:
            self._unsubscribe()

    def _spawn(self, coroutine: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _provider(self, connection: AgentConnection) -> Provider:
        provider = self.providers.get(connection.kind)
        if provider is None:
            raise unavailable(f"{connection.name} has no available provider.")
        return provider

    def _in_process_connections(self) -> list[str]:
        kinds = self.providers.in_process_kinds()
        with self.domain.db.read() as session:
            return list(session.scalars(select(AgentConnection.id).where(AgentConnection.kind.in_(kinds))))

    async def _dispatch_loop(self) -> None:
        while not self._stopping.is_set():
            # Cleared before reading: a job committed after the claim must still end the wait below.
            self._wake.clear()
            try:
                ids = await asyncio.to_thread(self._in_process_connections)
                claimed = ids and await asyncio.to_thread(self.domain.claim_message_job, ids, claim_state="submitted")
                if claimed:
                    task = self._spawn(self._execute(claimed))
                    self._running[claimed["run_id"]] = task
                    task.add_done_callback(lambda _t, r=claimed["run_id"]: self._running.pop(r, None))
                    continue
            except Exception:
                log.exception("dispatch claim failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except TimeoutError:
                pass

    async def _execute(self, job: dict[str, Any]) -> None:
        sink = RunSink(self.domain, job)
        connection = await asyncio.to_thread(self.domain.get_connection, job["connection_id"])
        try:
            await self._provider(connection).run(connection, job, sink)
            if not sink.done:
                await sink.complete()
        except asyncio.CancelledError:
            if not sink.done:
                await asyncio.shield(sink.fail("interrupted", "The service stopped during this run."))
            raise
        except ProviderError as error:
            await sink.fail(error.code, error.message)
        except Exception:
            log.exception("run failed", extra={"run_id": job["run_id"]})
            await sink.fail("provider_error", "The agent stopped unexpectedly.")

    async def cancel_run(self, run_id: str) -> schemas.RunOut:
        run, must_ask = await asyncio.to_thread(self.domain.request_cancel, run_id)
        if must_ask:
            job = await asyncio.to_thread(self._message_job, run_id)
            connection = await asyncio.to_thread(self.domain.get_connection, job.connection_id)
            provider = self._provider(connection)
            if provider.in_process:
                await provider.cancel(connection, run_id)
            else:
                await asyncio.to_thread(self.domain.enqueue_connector_cancel, run_id, connection.id, job.correlation_id)
        return run

    def _message_job(self, run_id: str) -> DispatchJob:
        with self.domain.db.read() as session:
            job = session.scalar(select(DispatchJob).where(DispatchJob.run_id == run_id, DispatchJob.kind == "message"))
            assert job is not None
            return job

    async def _maintenance_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.to_thread(self._maintain)
            except Exception:
                log.exception("maintenance failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=60)
            except TimeoutError:
                pass

    def _maintain(self) -> None:
        domain = self.domain
        domain.prune_events()
        domain.store.sweep_temporary()
        domain.remove_abandoned_uploads()
        domain.expire_connector_leases()
        with domain.store.retention_hold():
            domain.store.sweep_orphans(domain.known_object_keys())
        cutoff = later(-CONNECTOR_SILENCE_SECONDS)
        for connection in domain.list_connections():
            if connection.kind in ("hermes", "bridge") and connection.state == "connected":
                if connection.last_seen_at is None or connection.last_seen_at < cutoff:
                    domain.update_connection_state(connection.id, "disconnected")

    # Commands to a remote connector ------------------------------------------------------------------
    def _enqueue_command(self, connection_id: str, command: str, arguments: dict[str, Any]) -> str:
        with self.domain.db.write() as session:
            timestamp = now()
            job = DispatchJob(
                id=new_id(),
                kind="command",
                connection_id=connection_id,
                state="queued",
                correlation_id=new_id(),
                payload={"command": command, "arguments": arguments},
                created_at=timestamp,
                updated_at=timestamp,
            )
            session.add(job)
            return job.id

    def _command_result(self, job_id: str) -> tuple[str, dict[str, Any] | None]:
        with self.domain.db.read() as session:
            job = session.get(DispatchJob, job_id)
            assert job is not None
            return job.state, job.result

    def _abandon_command(self, job_id: str) -> None:
        with self.domain.db.write() as session:
            job = session.get(DispatchJob, job_id)
            if job is not None and job.state in ("queued", "claimed"):
                job.state = "failed"
                job.updated_at = now()

    async def command(
        self, connection: AgentConnection, command: str, arguments: dict[str, Any], *, timeout: float = COMMAND_TIMEOUT
    ) -> dict[str, Any]:
        provider = self._provider(connection)
        if provider.in_process:
            return await provider.automation(connection, command, arguments)
        job_id = await asyncio.to_thread(self._enqueue_command, connection.id, command, arguments)
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            state, result = await asyncio.to_thread(self._command_result, job_id)
            if state == "completed":
                return result or {}
            if state == "failed":
                detail = (result or {}).get("error") or "The scheduler rejected this change."
                raise ProviderError("provider_error", detail)
            await asyncio.sleep(0.2)
        await asyncio.to_thread(self._abandon_command, job_id)
        raise ProviderError("provider_unavailable", "The scheduler did not respond.")

    # Automations -------------------------------------------------------------------------------------
    async def sync_automations(self, connection: AgentConnection) -> None:
        result = await self.command(connection, "list", {})
        reports = [schemas.ConnectorJobReport.model_validate(job) for job in result.get("jobs", [])]
        await asyncio.to_thread(self.domain.sync_automations, connection.id, reports)

    async def automation_action(self, link_id: str, body: schemas.AutomationAction) -> schemas.AutomationSummary:
        link, connection = await asyncio.to_thread(self.domain.automation_link, link_id)
        self.domain.check_automation_revision(link, body.expected_revision)
        arguments: dict[str, Any] = {"job_id": link.provider_job_id}
        destination = body.destination_space_id
        if body.action == "redirect":
            if destination is None:
                raise ApiError(422, "destination_required", "Choose a destination space.")
            await asyncio.to_thread(self.domain.get_space, destination)
            arguments["destination"] = f"amsg:{destination}"
        try:
            await self.command(connection, body.action, arguments)
        except ProviderError as error:
            raise ApiError(503, error.code, error.message, retryable=True) from error
        apply = self.domain.apply_automation_result
        if body.action == "pause":
            return await asyncio.to_thread(apply, link_id, state="paused")
        if body.action == "resume":
            return await asyncio.to_thread(apply, link_id, state="active")
        if body.action == "redirect":
            return await asyncio.to_thread(apply, link_id, space_id=destination)
        if body.action == "remove":
            return await asyncio.to_thread(apply, link_id, state="removed", clear_space=True)
        return await asyncio.to_thread(apply, link_id)

    # Deletion ----------------------------------------------------------------------------------------
    async def _operations_loop(self) -> None:
        active: set[str] = set()

        async def drive(operation_id: str) -> None:
            try:
                await self._delete_space(operation_id)
            except Exception:
                log.exception("operation failed", extra={"operation_id": operation_id})
                await asyncio.to_thread(
                    self.domain.advance_operation,
                    operation_id,
                    "failed",
                    code="operation_error",
                    message="The deletion stopped. The space is unchanged.",
                )
            finally:
                active.discard(operation_id)

        while not self._stopping.is_set():
            try:
                for operation_id in await asyncio.to_thread(self.domain.open_operations):
                    if operation_id not in active:
                        active.add(operation_id)
                        self._spawn(drive(operation_id))
            except Exception:
                log.exception("operation scan failed")
            await asyncio.sleep(0.25)

    async def _delete_space(self, operation_id: str) -> None:
        """requested → provider_change_pending → provider_change_confirmed → space_deleted."""
        domain = self.domain
        operation = await asyncio.to_thread(domain.get_operation, operation_id)
        params = await asyncio.to_thread(self._operation_params, operation_id)
        policy = params["policy"]
        progress: dict[str, str] = dict(operation.progress.get("jobs", {}))
        await asyncio.to_thread(
            domain.advance_operation, operation_id, "provider_change_pending", progress={"jobs": progress}
        )
        links = await asyncio.to_thread(domain.links_for_space, operation.entity_id)
        with domain.db.read() as session:
            general_id = domain.general_space_id(session)
        failed = False
        for link in links:
            if progress.get(link.id) == "done":
                continue
            connection = await asyncio.to_thread(domain.get_connection, link.connection_id)
            try:
                if policy == "keep_and_move":
                    arguments = {"job_id": link.provider_job_id, "destination": f"amsg:{general_id}"}
                    await self.command(connection, "redirect", arguments)
                    await self._verify_redirect(connection, link.provider_job_id, general_id)
                    await asyncio.to_thread(domain.apply_automation_result, link.id, space_id=general_id)
                else:
                    await self.command(connection, "remove", {"job_id": link.provider_job_id})
                    await self._verify_removed(connection, link.provider_job_id)
                    await asyncio.to_thread(domain.apply_automation_result, link.id, state="removed", clear_space=True)
                progress[link.id] = "done"
            except ProviderError:
                progress[link.id] = "failed"
                failed = True
            await asyncio.to_thread(
                domain.advance_operation, operation_id, "provider_change_pending", progress={"jobs": progress}
            )
        if failed:
            await asyncio.to_thread(
                domain.advance_operation,
                operation_id,
                "failed",
                progress={"jobs": progress},
                code="provider_unavailable",
                message="The scheduler did not confirm every change. The space is kept.",
            )
            return
        await asyncio.to_thread(domain.advance_operation, operation_id, "provider_change_confirmed")
        remaining = await asyncio.to_thread(domain.links_for_space, operation.entity_id)
        if remaining:
            await asyncio.to_thread(
                domain.advance_operation,
                operation_id,
                "failed",
                code="automation_linked",
                message="A new automation was linked during deletion. Try again.",
            )
            return
        await asyncio.to_thread(domain.finish_delete, operation_id)

    def _operation_params(self, operation_id: str) -> dict[str, Any]:
        from ..db.models import Operation

        with self.domain.db.read() as session:
            row = session.get(Operation, operation_id)
            assert row is not None
            return row.params

    async def _verify_redirect(self, connection: AgentConnection, job_id: str, space_id: str) -> None:
        listed = await self.command(connection, "list", {})
        job = next((j for j in listed.get("jobs", []) if j["provider_job_id"] == job_id), None)
        if job is None or job.get("destination") != f"amsg:{space_id}":
            raise ProviderError("provider_unavailable", "The redirect could not be read back.")

    async def _verify_removed(self, connection: AgentConnection, job_id: str) -> None:
        listed = await self.command(connection, "list", {})
        if any(j["provider_job_id"] == job_id for j in listed.get("jobs", [])):
            raise ProviderError("provider_unavailable", "The job is still present on the scheduler.")
