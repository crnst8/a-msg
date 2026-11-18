"""Links between provider-owned scheduled jobs and spaces. Hermes stays the scheduler of record."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..db.models import AgentConnection, AutomationLink, Operation, Space
from ..errors import conflict, invalid, missing, stale
from .base import DomainBase, new_id, now
from .views import space_out

STALE_AFTER = timedelta(minutes=10)
PREFIX = "amsg:"


def _stale(link: AutomationLink, connection: AgentConnection | None) -> bool:
    if connection is None or connection.state not in ("connected", "degraded"):
        return True
    if link.last_synced_at is None:
        return True
    synced = datetime.fromisoformat(link.last_synced_at.replace("Z", "+00:00"))
    return datetime.now(UTC) - synced > STALE_AFTER


def destination_space(destination: str | None) -> str | None:
    """Delivery targets look like ``amsg:<space id>``; anything else is not ours to map."""
    if destination and destination.startswith(PREFIX):
        return destination[len(PREFIX) :].split(":")[0] or None
    return None


class AutomationsMixin(DomainBase):
    def _automation_out(self, session: Session, link: AutomationLink) -> schemas.AutomationSummary:
        connection = session.get(AgentConnection, link.connection_id)
        return schemas.AutomationSummary(
            id=link.id,
            name=link.name,
            schedule=link.schedule,
            timezone=link.timezone,
            next_run_at=link.next_run_at,
            last_run_at=link.last_run_at,
            last_outcome=link.last_outcome,
            state=link.state,
            stale=_stale(link, connection),
            space_id=link.space_id,
            connection_id=link.connection_id,
            provider_job_id=link.provider_job_id,
            last_synced_at=link.last_synced_at,
            revision=link.revision,
        )

    def automations(self, session: Session) -> list[schemas.AutomationSummary]:
        rows = session.scalars(
            select(AutomationLink).where(AutomationLink.state != "removed").order_by(AutomationLink.name)
        )
        return [self._automation_out(session, row) for row in rows]

    def list_automations(self) -> list[schemas.AutomationSummary]:
        with self.db.read() as session:
            return self.automations(session)

    def sync_automations(self, connection_id: str, jobs: list[schemas.ConnectorJobReport]) -> int:
        """Reconcile a full provider listing. Jobs absent from it no longer exist on the provider."""
        with self.db.write() as session:
            timestamp = now()
            seen: set[str] = set()
            existing = {
                row.provider_job_id: row
                for row in session.scalars(select(AutomationLink).where(AutomationLink.connection_id == connection_id))
            }
            pending = set(
                session.scalars(
                    select(Operation.entity_id).where(
                        Operation.kind == "delete_space",
                        Operation.state.not_in(("space_deleted", "archived", "cancelled")),
                    )
                )
            )
            for report in jobs:
                seen.add(report.provider_job_id)
                link = existing.get(report.provider_job_id)
                space_id = destination_space(report.destination)
                if space_id is not None:
                    space = session.get(Space, space_id)
                    if space is None or space.deleted_at is not None:
                        space_id = None
                if link is None:
                    link = AutomationLink(
                        id=new_id(),
                        connection_id=connection_id,
                        provider_job_id=report.provider_job_id,
                        space_id=None if space_id in pending else space_id,
                        name=report.name,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                    session.add(link)
                elif space_id is not None and space_id not in pending and link.space_id != space_id:
                    link.space_id = space_id
                    link.revision += 1
                link.name = report.name
                link.schedule = report.schedule
                link.timezone = report.timezone
                link.next_run_at = report.next_run_at
                link.last_run_at = report.last_run_at
                link.last_outcome = report.last_outcome
                link.state = report.state
                link.last_synced_at = timestamp
                link.updated_at = timestamp
            for job_id, link in existing.items():
                if job_id not in seen and link.state != "removed":
                    link.state = "removed"
                    link.updated_at = timestamp
                    link.revision += 1
            session.flush()
            self.emit(session, "automation.updated", None, {"automations": self._dump(session)})
            return len(jobs)

    def _dump(self, session: Session) -> list[dict[str, Any]]:
        return [row.model_dump() for row in self.automations(session)]

    def automation_link(self, link_id: str) -> tuple[AutomationLink, AgentConnection]:
        with self.db.read() as session:
            link = session.get(AutomationLink, link_id)
            if link is None or link.state == "removed":
                raise missing("Automation")
            connection = session.get(AgentConnection, link.connection_id)
            assert connection is not None
            return link, connection

    def check_automation_revision(self, link: AutomationLink, expected: int | None) -> None:
        if expected is not None and link.revision != expected:
            raise stale("This automation", link.revision)

    def apply_automation_result(
        self,
        link_id: str,
        *,
        state: str | None = None,
        space_id: str | None = None,
        clear_space: bool = False,
    ) -> schemas.AutomationSummary:
        with self.db.write() as session:
            link = session.get(AutomationLink, link_id)
            if link is None:
                raise missing("Automation")
            if state:
                link.state = state
            if space_id:
                space = self.space_or_404(session, space_id)
                if space.archived_at is not None:
                    raise conflict("space_archived", "Restore this space before using it as a destination.")
                link.space_id = space_id
            if clear_space:
                link.space_id = None
            link.revision += 1
            link.updated_at = now()
            session.flush()
            out = self._automation_out(session, link)
            self.emit(session, "automation.updated", link.id, {"automations": self._dump(session)})
            if link.space_id:
                space = session.get(Space, link.space_id)
                if space is not None:
                    self.emit(session, "space.updated", space.id, {"space": space_out(session, space).model_dump()})
            return out

    def links_for_space(self, space_id: str) -> list[AutomationLink]:
        with self.db.read() as session:
            return list(
                session.scalars(
                    select(AutomationLink).where(AutomationLink.space_id == space_id, AutomationLink.state != "removed")
                )
            )

    # Deletion operation ------------------------------------------------------------------------------
    def begin_delete(self, space_id: str, body: schemas.DeletePolicy, actor: str) -> schemas.OperationOut:
        with self.db.write() as session:
            space = self.space_or_404(session, space_id)
            if space.kind == "general":
                raise conflict("general_immutable", "General cannot be deleted.")
            if space.revision != body.expected_revision:
                raise stale("This space", space.revision)

            def action() -> dict[str, Any]:
                pending = session.scalar(
                    select(Operation).where(
                        Operation.kind == "delete_space",
                        Operation.entity_id == space_id,
                        Operation.state.not_in(("space_deleted", "archived", "cancelled")),
                    )
                )
                if pending is not None:
                    raise conflict("operation_pending", "A deletion is already in progress.", operation_id=pending.id)
                linked = session.scalars(
                    select(AutomationLink).where(AutomationLink.space_id == space_id, AutomationLink.state != "removed")
                ).all()
                if linked and body.policy == "delete":
                    raise conflict(
                        "automation_linked",
                        "Choose what happens to the automations that deliver here.",
                        automations=[link.id for link in linked],
                    )
                timestamp = now()
                operation = Operation(
                    id=new_id(),
                    kind="delete_space",
                    state="requested",
                    entity_id=space_id,
                    params={"policy": body.policy},
                    progress={"jobs": {link.id: "pending" for link in linked}},
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                session.add(operation)
                session.flush()
                space.revision += 1
                self.emit(session, "operation.updated", operation.id, {"operation": self._op(operation)})
                self.emit(session, "space.updated", space.id, {"space": space_out(session, space).model_dump()})
                return {"operation_id": operation.id}

            result, _ = self.idempotent(
                session, "delete_space", body.idempotency_key, actor, {"space": space_id, "policy": body.policy}, action
            )
            operation = session.get(Operation, result["operation_id"])
            assert operation is not None
            return schemas.OperationOut.model_validate(operation)

    def _op(self, operation: Operation) -> dict[str, Any]:
        return schemas.OperationOut.model_validate(operation).model_dump()

    def get_operation(self, operation_id: str) -> schemas.OperationOut:
        with self.db.read() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise missing("Operation")
            return schemas.OperationOut.model_validate(operation)

    def open_operations(self) -> list[str]:
        with self.db.read() as session:
            return list(
                session.scalars(
                    select(Operation.id).where(
                        Operation.state.in_(("requested", "provider_change_pending", "provider_change_confirmed"))
                    )
                )
            )

    def advance_operation(
        self,
        operation_id: str,
        state: str,
        *,
        progress: dict[str, Any] | None = None,
        code: str | None = None,
        message: str | None = None,
    ) -> None:
        with self.db.write() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise missing("Operation")
            operation.state = state
            if progress is not None:
                operation.progress = progress
            operation.error_code = code
            operation.error_message = message
            operation.updated_at = now()
            session.flush()
            self.emit(session, "operation.updated", operation.id, {"operation": self._op(operation)})
            space = session.get(Space, operation.entity_id) if operation.entity_id else None
            if space is not None and space.deleted_at is None:
                self.emit(session, "space.updated", space.id, {"space": space_out(session, space).model_dump()})

    def retry_operation(self, operation_id: str) -> schemas.OperationOut:
        with self.db.write() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise missing("Operation")
            if operation.state != "failed":
                raise invalid("not_retryable", "Only a failed operation can be retried.")
            operation.state = "requested"
            operation.error_code = None
            operation.error_message = None
            operation.updated_at = now()
            session.flush()
            self.emit(session, "operation.updated", operation.id, {"operation": self._op(operation)})
            return schemas.OperationOut.model_validate(operation)

    def cancel_operation(self, operation_id: str) -> schemas.OperationOut:
        with self.db.write() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise missing("Operation")
            if operation.state != "failed":
                raise invalid("not_cancellable", "Only a failed deletion can be dismissed.")
            operation.state = "cancelled"
            operation.updated_at = now()
            session.flush()
            self.emit(session, "operation.updated", operation.id, {"operation": self._op(operation)})
            space = session.get(Space, operation.entity_id) if operation.entity_id else None
            if space is not None:
                self.emit(session, "space.updated", space.id, {"space": space_out(session, space).model_dump()})
            return schemas.OperationOut.model_validate(operation)

    def finish_delete(self, operation_id: str, *, archived: bool = False) -> None:
        with self.db.write() as session:
            operation = session.get(Operation, operation_id)
            assert operation is not None
            space = session.get(Space, operation.entity_id) if operation.entity_id else None
            if space is None:
                return
            linked = session.scalars(
                select(AutomationLink).where(AutomationLink.space_id == space.id, AutomationLink.state != "removed")
            ).all()
            if linked:
                raise conflict("automation_linked", "An automation still delivers to this space.")
            timestamp = now()
            space.deleted_at = timestamp
            space.updated_at = timestamp
            space.revision += 1
            operation.state = "space_deleted"
            operation.updated_at = timestamp
            session.flush()
            self.emit(session, "space.deleted", space.id, {"space_id": space.id})
            self.emit(session, "operation.updated", operation.id, {"operation": self._op(operation)})
