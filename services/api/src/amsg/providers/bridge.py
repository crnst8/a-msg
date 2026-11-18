"""Provider for connectors that run elsewhere, such as the Hermes plugin. The connector drives the run."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .base import Provider, RunSink

if TYPE_CHECKING:
    from ..db.models import AgentConnection
    from ..domain import Domain

FRESH = timedelta(seconds=45)


class BridgeProvider(Provider):
    in_process = False

    def __init__(self, domain: Domain) -> None:
        self.domain = domain

    def features(self, connection: AgentConnection) -> dict[str, Any]:
        return connection.capabilities or {}

    async def run(self, connection: AgentConnection, job: dict[str, Any], sink: RunSink) -> None:
        raise NotImplementedError("Bridged runs are claimed by their connector.")

    async def test(self, connection: AgentConnection) -> list[dict[str, Any]]:
        seen = connection.last_seen_at
        if seen is None:
            return [{"name": "connector", "ok": False, "detail": "The connector has not connected yet."}]
        moment = datetime.fromisoformat(seen.replace("Z", "+00:00"))
        fresh = datetime.now(UTC) - moment < FRESH
        detail = "Polling for work" if fresh else f"Last seen {seen}"
        return [{"name": "connector", "ok": fresh, "detail": detail}]
