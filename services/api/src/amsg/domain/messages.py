"""Message acceptance, run lifecycle and the durable dispatch queue."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import schemas
from ..db.models import AgentConnection, Artifact, DispatchJob, Message, MessageArtifact, Run, Space
from ..errors import conflict, invalid, missing, stale
from .base import ACTIVE_RUN_STATES, TERMINAL_RUN_STATES, DomainBase, later, new_id, now
from .views import message_out, messages_out, run_out, space_out

MAX_PAGE = 200


class MessagesMixin(DomainBase):
    def list_messages(self, space_id: str, *, before: int | None, limit: int) -> schemas.MessagePage:
        limit = min(max(limit, 1), MAX_PAGE)
        with self.db.read() as session:
            self.space_or_404(session, space_id)
            query = select(Message).where(Message.space_id == space_id)
            if before is not None:
                query = query.where(Message.seq < before)
            rows = list(session.scalars(query.order_by(Message.seq.desc()).limit(limit + 1)))
            more = len(rows) > limit
            rows = rows[:limit]
            rows.reverse()
            return schemas.MessagePage(
                items=messages_out(session, rows), next_before=rows[0].seq if more and rows else None
            )

    def _attach(self, session: Session, message: Message, artifact_ids: list[str]) -> None:
        for ordinal, artifact_id in enumerate(dict.fromkeys(artifact_ids)):
            artifact = session.get(Artifact, artifact_id)
            if artifact is None or artifact.state != "ready":
                raise invalid("artifact_missing", "An attachment is no longer available.", artifact_id=artifact_id)
            if artifact.origin_message_id is None:
                artifact.origin_message_id = message.id
                artifact.origin_space_id = message.space_id
            session.add(MessageArtifact(message_id=message.id, artifact_id=artifact_id, ordinal=ordinal))

    def accept_message(self, space_id: str, body: schemas.MessageCreate) -> schemas.Receipt:
        """Commit the message, run, dispatch job and event together; inference happens later."""
        text = body.markdown.strip()
        if not text and not body.artifact_ids:
            raise invalid("empty_message", "A message needs text or an attachment.")
        with self.db.write() as session:
            existing = session.scalar(select(Message).where(Message.client_message_id == body.client_message_id))
            if existing is not None:
                attached = list(
                    session.scalars(
                        select(MessageArtifact.artifact_id)
                        .where(MessageArtifact.message_id == existing.id)
                        .order_by(MessageArtifact.ordinal)
                    )
                )
                if existing.space_id != space_id or existing.markdown != text or attached != body.artifact_ids:
                    raise conflict(
                        "idempotency_conflict", "This client message ID was already used with different content."
                    )
                run = session.get(Run, existing.run_id) if existing.run_id else None
                assert run is not None
                return schemas.Receipt(message=message_out(session, existing), run=run_out(run), duplicate=True)
            space = self.space_or_404(session, space_id)
            if space.archived_at is not None:
                raise conflict("space_archived", "Restore this space before sending to it.")
            if space.agent_connection_id is None:
                raise conflict("no_agent", "This space has no connected agent.")
            timestamp = now()
            run = Run(id=new_id(), space_id=space.id, state="queued", accepted_at=timestamp)
            session.add(run)
            session.flush()
            seq = self.next_message_seq(session)
            message = Message(
                id=new_id(),
                seq=seq,
                space_id=space.id,
                role="user",
                markdown=text,
                status="queued_server",
                client_message_id=body.client_message_id,
                run_id=run.id,
                meta={},
                created_at=timestamp,
                updated_at=timestamp,
            )
            session.add(message)
            session.flush()
            self._attach(session, message, body.artifact_ids)
            run.request_message_id = message.id
            self.touch_space(session, space, seq, read=True)
            session.add(
                DispatchJob(
                    id=new_id(),
                    kind="message",
                    space_id=space.id,
                    run_id=run.id,
                    message_id=message.id,
                    connection_id=space.agent_connection_id,
                    state="queued",
                    correlation_id=run.id,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            session.flush()
            out = message_out(session, message)
            self.emit(session, "message.accepted", message.id, {"message": out.model_dump()})
            return schemas.Receipt(message=out, run=run_out(run))

    # Queue inspection -----------------------------------------------------------------------------
    def _queued(self, session: Session, message_id: str) -> tuple[Message, DispatchJob]:
        message = session.get(Message, message_id)
        if message is None or message.role != "user":
            raise missing("Queued message")
        job = session.scalar(
            select(DispatchJob).where(DispatchJob.message_id == message_id, DispatchJob.kind == "message")
        )
        if job is None or job.state != "queued":
            raise conflict("already_dispatched", "This message has already been sent to the agent.")
        return message, job

    def get_queued(self, message_id: str) -> schemas.MessageOut:
        with self.db.read() as session:
            message, _ = self._queued(session, message_id)
            return message_out(session, message)

    def edit_queued(self, message_id: str, body: schemas.QueuedMessagePatch) -> schemas.MessageOut:
        with self.db.write() as session:
            message, _ = self._queued(session, message_id)
            if message.revision != body.expected_revision:
                raise stale("This message", message.revision)
            message.markdown = body.markdown.strip()
            message.revision += 1
            message.updated_at = now()
            session.flush()
            out = message_out(session, message)
            self.emit(session, "message.updated", message.id, {"message": out.model_dump()})
            return out

    def remove_queued(self, message_id: str) -> None:
        with self.db.write() as session:
            message, job = self._queued(session, message_id)
            run = session.get(Run, message.run_id) if message.run_id else None
            space_id = message.space_id
            session.delete(job)
            session.delete(message)
            session.flush()
            if run is not None:
                session.delete(run)
            self.emit(session, "message.removed", message_id, {"message_id": message_id, "space_id": space_id})

    # Claiming -------------------------------------------------------------------------------------
    def active_run_count(self, session: Session) -> int:
        return session.scalar(select(func.count()).select_from(Run).where(Run.state.in_(ACTIVE_RUN_STATES))) or 0

    def _claimable(self, session: Session, connection_ids: list[str] | None) -> DispatchJob | None:
        query = select(DispatchJob).where(DispatchJob.state == "queued", DispatchJob.kind == "message")
        if connection_ids is not None:
            query = query.where(DispatchJob.connection_id.in_(connection_ids))
        busy = select(Run.space_id).where(Run.state.in_(ACTIVE_RUN_STATES))
        query = query.where(DispatchJob.space_id.not_in(busy)).order_by(DispatchJob.created_at, DispatchJob.id)
        return session.scalars(query.limit(1)).first()

    def claim_message_job(
        self, connection_ids: list[str] | None, *, claim_state: str = "claimed"
    ) -> dict[str, Any] | None:
        """Claim the next job in a free space, within the global concurrency bound."""
        with self.db.write() as session:
            if self.active_run_count(session) >= self.settings.max_concurrent_runs:
                return None
            job = self._claimable(session, connection_ids)
            if job is None:
                return None
            run = session.get(Run, job.run_id)
            request = session.get(Message, job.message_id)
            space = session.get(Space, job.space_id)
            connection = session.get(AgentConnection, job.connection_id)
            assert run and request and space and connection
            timestamp = now()
            job.state = claim_state
            job.claimed_at = timestamp
            job.lease_until = later(self.settings.connector_lease_seconds)
            job.attempts += 1
            job.updated_at = timestamp
            run.state = "running"
            run.started_at = timestamp
            run.revision += 1
            request.status = "running"
            request.revision += 1
            request.updated_at = timestamp
            seq = self.next_message_seq(session)
            reply = Message(
                id=new_id(),
                seq=seq,
                space_id=space.id,
                role="agent",
                markdown="",
                status="running",
                run_id=run.id,
                source_message_id=request.id,
                author=connection.name,
                meta={"attempt": run.attempt} if run.attempt > 1 else {},
                created_at=timestamp,
                updated_at=timestamp,
            )
            session.add(reply)
            session.flush()
            run.reply_message_id = reply.id
            self.touch_space(session, space, seq)
            self.emit(
                session,
                "message.updated",
                request.id,
                {"message": message_out(session, request).model_dump()},
            )
            self.emit(
                session,
                "run.started",
                run.id,
                {"run": run_out(run).model_dump(), "message": message_out(session, reply).model_dump()},
            )
            return {
                "job_id": job.id,
                "run_id": run.id,
                "space_id": space.id,
                "connection_id": connection.id,
                "connection_kind": connection.kind,
                "correlation_id": job.correlation_id,
                "reply_message_id": reply.id,
                "request_message_id": request.id,
                "context": self.turn_context(session, space, request, connection),
            }

    def next_retry_ready(self) -> bool:
        with self.db.read() as session:
            return self._claimable(session, None) is not None

    def acknowledge_dispatch(self, job_id: str, provider_run_id: str | None) -> None:
        with self.db.write() as session:
            job = session.get(DispatchJob, job_id)
            if job is None:
                raise missing("Dispatch job")
            if job.state == "claimed":
                job.state = "submitted"
                job.updated_at = now()
            run = session.get(Run, job.run_id) if job.run_id else None
            if run is not None and provider_run_id:
                run.provider_run_id = provider_run_id

    # Streaming ------------------------------------------------------------------------------------
    def _reply(self, session: Session, run_id: str) -> tuple[Run, Message]:
        run = session.get(Run, run_id)
        if run is None or run.reply_message_id is None:
            raise missing("Run")
        reply = session.get(Message, run.reply_message_id)
        assert reply is not None
        return run, reply

    def append_delta(self, run_id: str, text: str) -> None:
        if not text:
            return
        with self.db.write() as session:
            run, reply = self._reply(session, run_id)
            if run.state in TERMINAL_RUN_STATES:
                return
            offset = len(reply.markdown)
            reply.markdown += text
            reply.revision += 1
            reply.updated_at = now()
            self.emit(
                session,
                "run.delta",
                run.id,
                {
                    "run_id": run.id,
                    "message_id": reply.id,
                    "space_id": reply.space_id,
                    "revision": reply.revision,
                    "offset": offset,
                    "append": text,
                },
            )

    def replace_body(self, run_id: str, markdown: str) -> None:
        """Provider-side edits arrive as a whole body rather than an append."""
        with self.db.write() as session:
            run, reply = self._reply(session, run_id)
            if run.state in TERMINAL_RUN_STATES or reply.markdown == markdown:
                return
            reply.markdown = markdown
            reply.revision += 1
            reply.updated_at = now()
            session.flush()
            self.emit(session, "message.updated", reply.id, {"message": message_out(session, reply).model_dump()})

    def set_activity(self, run_id: str, text: str | None) -> None:
        with self.db.write() as session:
            run, reply = self._reply(session, run_id)
            if run.state in TERMINAL_RUN_STATES or reply.activity == text:
                return
            reply.activity = text
            reply.revision += 1
            reply.updated_at = now()
            session.flush()
            self.emit(session, "message.updated", reply.id, {"message": message_out(session, reply).model_dump()})

    def _finish(
        self,
        session: Session,
        run: Run,
        reply: Message | None,
        state: str,
        *,
        code: str | None = None,
        message: str | None = None,
    ) -> None:
        timestamp = now()
        run.state = state
        run.finished_at = timestamp
        run.error_code = code
        run.error_message = message
        run.revision += 1
        request = session.get(Message, run.request_message_id) if run.request_message_id else None
        if request is not None and request.status not in TERMINAL_RUN_STATES:
            request.status = state
            request.revision += 1
            request.updated_at = timestamp
        job = session.scalar(select(DispatchJob).where(DispatchJob.run_id == run.id, DispatchJob.kind == "message"))
        if job is not None:
            job.state = {"completed": "completed", "cancelled": "cancelled", "delivery_uncertain": "uncertain"}.get(
                state, "failed"
            )
            job.updated_at = timestamp
        space = session.get(Space, run.space_id)
        if reply is not None:
            reply.status = state
            reply.activity = None
            reply.revision += 1
            reply.updated_at = timestamp
            if space is not None:
                self.touch_space(session, space, reply.seq)
        session.flush()
        kind = {"completed": "run.completed", "cancelled": "run.cancelled"}.get(state, "run.failed")
        payload: dict[str, Any] = {"run": run_out(run).model_dump()}
        if reply is not None:
            payload["message"] = message_out(session, reply).model_dump()
        if request is not None:
            payload["request"] = message_out(session, request).model_dump()
        if space is not None:
            payload["space"] = space_out(session, space).model_dump()
        self.emit(session, kind, run.id, payload)

    def complete_run(self, run_id: str, markdown: str | None = None, artifact_ids: list[str] | None = None) -> None:
        with self.db.write() as session:
            run, reply = self._reply(session, run_id)
            if run.state in TERMINAL_RUN_STATES:
                return
            if markdown is not None:
                reply.markdown = markdown
            for offset, artifact_id in enumerate(artifact_ids or []):
                self._link_artifact(session, reply, artifact_id, offset)
            self._finish(session, run, reply, "completed")

    def fail_run(self, run_id: str, code: str, message: str) -> None:
        with self.db.write() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise missing("Run")
            if run.state in TERMINAL_RUN_STATES:
                return
            reply = session.get(Message, run.reply_message_id) if run.reply_message_id else None
            self._finish(session, run, reply, "failed", code=code, message=message)

    def confirm_cancelled(self, run_id: str) -> None:
        with self.db.write() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise missing("Run")
            if run.state in TERMINAL_RUN_STATES:
                return
            reply = session.get(Message, run.reply_message_id) if run.reply_message_id else None
            self._finish(session, run, reply, "cancelled")

    def mark_uncertain(self, session: Session, run: Run) -> None:
        reply = session.get(Message, run.reply_message_id) if run.reply_message_id else None
        self._finish(
            session,
            run,
            reply,
            "delivery_uncertain",
            code="delivery_uncertain",
            message="The agent may or may not have received this. Retry to send it again.",
        )

    def _link_artifact(self, session: Session, message: Message, artifact_id: str, ordinal: int) -> None:
        artifact = session.get(Artifact, artifact_id)
        if artifact is None or artifact.state != "ready":
            raise invalid("artifact_missing", "That artifact is not available.")
        exists = session.get(MessageArtifact, (message.id, artifact_id))
        if exists is None:
            session.add(MessageArtifact(message_id=message.id, artifact_id=artifact_id, ordinal=ordinal))
        if artifact.origin_message_id is None:
            artifact.origin_message_id = message.id
            artifact.origin_space_id = message.space_id
        session.flush()

    def attach_to_message(self, message_id: str, artifact_id: str) -> None:
        with self.db.write() as session:
            message = session.get(Message, message_id)
            if message is None:
                raise missing("Message")
            self._link_artifact(session, message, artifact_id, 0)
            message.revision += 1
            message.updated_at = now()
            session.flush()
            self.emit(session, "message.updated", message.id, {"message": message_out(session, message).model_dump()})

    # Cancel and retry -----------------------------------------------------------------------------
    def request_cancel(self, run_id: str) -> tuple[schemas.RunOut, bool]:
        """Returns the run and whether the provider must be asked to stop."""
        with self.db.write() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise missing("Run")
            if run.state in TERMINAL_RUN_STATES or run.state == "cancel_requested":
                return run_out(run), False
            if run.state == "queued":
                self._finish(session, run, None, "cancelled")
                return run_out(run), False
            run.state = "cancel_requested"
            run.revision += 1
            reply = session.get(Message, run.reply_message_id) if run.reply_message_id else None
            if reply is not None:
                reply.status = "cancel_requested"
                reply.revision += 1
                reply.updated_at = now()
                session.flush()
                self.emit(session, "message.updated", reply.id, {"message": message_out(session, reply).model_dump()})
            return run_out(run), True

    def enqueue_connector_cancel(self, run_id: str, connection_id: str, correlation_id: str) -> None:
        with self.db.write() as session:
            timestamp = now()
            session.add(
                DispatchJob(
                    id=new_id(),
                    kind="cancel",
                    run_id=run_id,
                    connection_id=connection_id,
                    state="queued",
                    correlation_id=correlation_id,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )

    def retry_run(self, run_id: str) -> schemas.RunOut:
        with self.db.write() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise missing("Run")
            if run.state not in ("failed", "cancelled", "delivery_uncertain"):
                raise conflict("not_retryable", "Only a finished failed or stopped run can be retried.")
            later_attempt = session.scalar(select(Run).where(Run.attempt_of == run.id))
            if later_attempt is not None:
                return run_out(later_attempt)
            space = self.space_or_404(session, run.space_id)
            if space.agent_connection_id is None:
                raise conflict("no_agent", "This space has no connected agent.")
            timestamp = now()
            attempt = Run(
                id=new_id(),
                space_id=run.space_id,
                request_message_id=run.request_message_id,
                state="queued",
                attempt_of=run.id,
                attempt=run.attempt + 1,
                accepted_at=timestamp,
            )
            session.add(attempt)
            session.flush()
            session.add(
                DispatchJob(
                    id=new_id(),
                    kind="message",
                    space_id=run.space_id,
                    run_id=attempt.id,
                    message_id=run.request_message_id,
                    connection_id=space.agent_connection_id,
                    state="queued",
                    correlation_id=attempt.id,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            session.flush()
            self.emit(session, "run.accepted", attempt.id, {"run": run_out(attempt).model_dump()})
            return run_out(attempt)

    # Agent posts that are not replies to a run ------------------------------------------------------
    def post_agent_message(
        self,
        space_id: str,
        markdown: str,
        *,
        author: str,
        meta: dict[str, Any] | None = None,
        artifact_ids: list[str] | None = None,
        dedupe_key: str | None = None,
    ) -> schemas.MessageOut:
        with self.db.write() as session:
            if dedupe_key:
                prior = session.scalar(select(Message).where(Message.client_message_id == dedupe_key))
                if prior is not None:
                    return message_out(session, prior)
            space = self.space_or_404(session, space_id)
            timestamp = now()
            seq = self.next_message_seq(session)
            message = Message(
                id=new_id(),
                seq=seq,
                space_id=space.id,
                role="agent",
                markdown=markdown,
                status="completed",
                client_message_id=dedupe_key,
                author=author,
                meta=meta or {},
                created_at=timestamp,
                updated_at=timestamp,
            )
            session.add(message)
            session.flush()
            for offset, artifact_id in enumerate(artifact_ids or []):
                self._link_artifact(session, message, artifact_id, offset)
            self.touch_space(session, space, seq)
            session.flush()
            out = message_out(session, message)
            self.emit(
                session,
                "message.accepted",
                message.id,
                {"message": out.model_dump(), "space": space_out(session, space).model_dump()},
            )
            return out

    # Recovery -------------------------------------------------------------------------------------
    def reconcile_after_restart(self, in_process_kinds: tuple[str, ...]) -> None:
        """Resolve work left by an earlier process without risking a duplicate external action."""
        with self.db.write() as session:
            rows = session.execute(
                select(Run, DispatchJob, AgentConnection)
                .join(DispatchJob, DispatchJob.run_id == Run.id)
                .join(AgentConnection, AgentConnection.id == DispatchJob.connection_id)
                .where(Run.state.in_(ACTIVE_RUN_STATES), DispatchJob.kind == "message")
            ).all()
            for run, job, connection in rows:
                reply = session.get(Message, run.reply_message_id) if run.reply_message_id else None
                if connection.kind in in_process_kinds:
                    self._finish(
                        session,
                        run,
                        reply,
                        "failed",
                        code="interrupted",
                        message="The service restarted during this run. The partial reply is kept.",
                    )
                elif job.state == "claimed":
                    self.mark_uncertain(session, run)

    def expire_connector_leases(self) -> int:
        """A connector that claimed work and went silent leaves delivery uncertain, not retried."""
        with self.db.write() as session:
            timestamp = now()
            rows = session.execute(
                select(Run, DispatchJob)
                .join(DispatchJob, DispatchJob.run_id == Run.id)
                .where(
                    Run.state.in_(ACTIVE_RUN_STATES),
                    DispatchJob.kind == "message",
                    DispatchJob.lease_until.is_not(None),
                    DispatchJob.lease_until < timestamp,
                )
            ).all()
            for run, _job in rows:
                self.mark_uncertain(session, run)
            return len(rows)

    def extend_lease(self, session: Session, run_id: str) -> None:
        job = session.scalar(select(DispatchJob).where(DispatchJob.run_id == run_id, DispatchJob.kind == "message"))
        if job is not None:
            job.lease_until = later(self.settings.connector_lease_seconds)
