"""Snapshot, replay, retention and health."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from .. import __version__, schemas
from ..db.models import AgentConnection, DispatchJob, Event, Message, Run
from ..errors import ApiError
from .base import ACTIVE_RUN_STATES, DomainBase
from .views import connection_out, messages_out

PRUNED_KEY = "events_pruned_through"
REPLAY_BATCH = 500


class ResyncRequired(ApiError):
    def __init__(self, cursor: int) -> None:
        super().__init__(
            410, "resync_required", "This event cursor is no longer available.", details={"cursor": cursor}
        )


class EventsMixin(DomainBase):
    def service_id(self) -> str:
        with self.db.read() as session:
            value = self.meta_get(session, "service_id")
            assert value is not None
            return value

    def snapshot(self) -> schemas.Snapshot:
        """One read transaction covers every list and the cursor, so they describe a single moment."""
        with self.db.read() as session:
            cursor = session.scalar(select(func.max(Event.seq))) or 0
            spaces = self._spaces(session, False)  # type: ignore[attr-defined]
            connections = [
                connection_out(session, row)
                for row in session.scalars(select(AgentConnection).order_by(AgentConnection.created_at))
            ]
            active = session.scalars(select(Run).where(Run.state.in_(ACTIVE_RUN_STATES)))
            queued = session.scalars(select(Message).where(Message.status == "queued_server").order_by(Message.seq))
            return schemas.Snapshot(
                cursor=cursor,
                service_id=self.meta_get(session, "service_id") or "",
                spaces=spaces,
                connections=connections,
                automations=self.automations(session),  # type: ignore[attr-defined]
                active_runs=[
                    schemas.ActiveRun(id=r.id, space_id=r.space_id, state=r.state, reply_message_id=r.reply_message_id)
                    for r in active
                ],
                queued_messages=messages_out(session, queued),
            )

    def check_cursor(self, cursor: int) -> None:
        with self.db.read() as session:
            pruned = int(self.meta_get(session, PRUNED_KEY) or 0)
            top = session.scalar(select(func.max(Event.seq))) or 0
            if cursor < pruned or cursor > top:
                raise ResyncRequired(top)

    def events_after(self, cursor: int) -> list[Event]:
        with self.db.read() as session:
            return list(
                session.scalars(select(Event).where(Event.seq > cursor).order_by(Event.seq).limit(REPLAY_BATCH))
            )

    def prune_events(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=self.settings.event_retention_days)
        stamp = cutoff.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        with self.db.write() as session:
            top = session.scalar(select(func.max(Event.seq)).where(Event.created_at < stamp))
            if top is None:
                return 0
            result = session.execute(delete(Event).where(Event.seq <= top))
            self.meta_set(session, PRUNED_KEY, str(top))
            return result.rowcount or 0

    def health(self) -> schemas.Health:
        with self.db.read() as session:
            quick = session.connection().exec_driver_sql("PRAGMA quick_check(1)").scalar()
            queued = session.scalar(select(func.count()).select_from(DispatchJob).where(DispatchJob.state == "queued"))
            active = session.scalar(select(func.count()).select_from(Run).where(Run.state.in_(ACTIVE_RUN_STATES)))
            connections = [
                {"id": c.id, "name": c.name, "kind": c.kind, "state": c.state, "last_seen_at": c.last_seen_at}
                for c in session.scalars(select(AgentConnection))
            ]
            return schemas.Health(
                ok=quick == "ok",
                version=__version__,
                database={"status": quick},
                connections=connections,
                queue={"queued": queued or 0, "active": active or 0},
            )
