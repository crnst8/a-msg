# ruff: noqa: E501
"""Development-only provider. Labelled in every API response and refused outside development mode.

It answers by keyword so the interface can be exercised without an agent: "health", "fail", "slow", "file",
"table". It exists for fixtures and tests and must never stand in for a real integration.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import Provider, ProviderError, RunSink

if TYPE_CHECKING:
    from ..db.models import AgentConnection
    from ..domain import Domain

HEALTH = """**Health sweep done** — read-only, no changes made. Full table saved to `workspace/projects/vps-health/vps-health-2026-11-05.md`.

**Coverage:** 12 hosts found in the SSH config and the server runbook; 10 checked, 2 not reachable. Duplicate aliases consolidated, router and switch aliases excluded.

**Nothing is down.** All 10 reachable hosts reported zero failed systemd units and their core services active (SSH everywhere, plus nginx/Postgres/Docker/mail where applicable). No OOM or disk-I/O errors in the logs.

**One host needs attention soon:**

`media-1` — root filesystem at 95%, only ~768 MiB free, and ~7,500 Docker error entries in the last 24 hours. Containers are still running, but this is the one that tips into an outage if it's left.

Worth a look, not urgent:

- `mail-1` — /var at 86%, ~320 MiB free
- `web-2` — 1.84 GiB swap occupied (sampled swap activity was zero) plus recurring cron mail errors
- `cache-1` — 47 cache-refresh failures in 24 hours, latest run succeeded

Lower priority: update-service network timeouts on `web-1`, and one unclassified kernel error entry each on `db-1/db-2`.

**The two that couldn't be checked:**

`staging-1` timed out, but it is documented as stopped, so this is expected.

`backup-1` — SSH authentication failed. That one isn't explained by the runbook, and it's the only unexplained gap.

**Two things I have not done:** no remediation (freeing disk on media-1 wasn't asked for), and no deep-dive on the Docker errors — the check summarised them rather than dumping logs.

Want me to have Codex do a read-only follow-up on media-1's disk usage, and separately investigate why backup-1 rejects the key? Job ID for reference is vps-health-20261105-0359."""

TABLE = """## Disk usage by host

| Host | Mount | Used | Free | Trend (7 days) | Last checked | Notes |
|---|---|---:|---:|---|---|---|
| media-1 | / | 95% | 768 MiB | +3.2% | 2026-11-05 03:59 UTC | Docker logs growing fastest; rotate before the weekend |
| mail-1 | /var | 86% | 320 MiB | +0.4% | 2026-11-05 03:59 UTC | Mail index |
| web-2 | / | 61% | 18 GiB | -0.2% | 2026-11-05 03:59 UTC | Swap in use |

```python
def usage(path: str) -> float:
    stat = os.statvfs(path)
    total = stat.f_blocks * stat.f_frsize
    free = stat.f_bavail * stat.f_frsize
    return 1 - free / total  # fraction used, excluding the root reservation on ext4 filesystems by default
```
"""


class FakeProvider(Provider):
    def __init__(self, domain: Domain) -> None:
        self.domain = domain
        self._cancel: dict[str, asyncio.Event] = {}
        self.unavailable_jobs: set[str] = set()

    def features(self, connection: AgentConnection) -> dict[str, Any]:
        return {
            "streaming": True,
            "cancel": bool((connection.config or {}).get("cancel", True)),
            "artifacts": True,
            "automations": True,
            "sessions": True,
            "development_only": True,
        }

    async def _stream(self, sink: RunSink, text: str, *, speed: float = 0.012) -> bool:
        event = self._cancel[sink.run_id]
        words = text.split(" ")
        for index, word in enumerate(words):
            if event.is_set():
                return False
            await sink.delta(word + (" " if index < len(words) - 1 else ""))
            await asyncio.sleep(speed)
        return True

    async def run(self, connection: AgentConnection, job: dict[str, Any], sink: RunSink) -> None:
        text = job["context"]["text"].lower()
        self._cancel.setdefault(sink.run_id, asyncio.Event())
        try:
            if "fail" in text:
                await self._stream(sink, "Starting the check on all hosts. The first eleven responded, then")
                await sink.fail("provider_error", "The agent lost its connection to the model.")
            elif "slow" in text:
                await sink.activity("Checking 19 hosts…")
                await asyncio.sleep(1.0)
                await sink.activity(None)
                if await self._stream(sink, " ".join([HEALTH] * 3), speed=0.03):
                    await sink.complete()
                else:
                    await self._stopped(connection, sink)
            elif "health" in text:
                await sink.activity("Checking 19 hosts…")
                await asyncio.sleep(0.4)
                await sink.activity(None)
                if await self._stream(sink, HEALTH):
                    await sink.artifact_bytes("vps-health-2026-11-05.md", HEALTH.encode())
                    await sink.complete()
                else:
                    await self._stopped(connection, sink)
            elif "table" in text:
                await self._stream(sink, TABLE, speed=0.004)
                await sink.complete()
            elif "file" in text:
                await self._stream(sink, "Here is the file.")
                await sink.artifact_bytes("notes.txt", b"Generated by the development provider.\n")
                await sink.complete()
            else:
                names = ", ".join(a["name"] for a in job["context"]["artifacts"])
                suffix = f" Received: {names}." if names else ""
                if await self._stream(sink, f"Received.{suffix}"):
                    await sink.complete()
                else:
                    await self._stopped(connection, sink)
        finally:
            self._cancel.pop(sink.run_id, None)

    async def _stopped(self, connection: AgentConnection, sink: RunSink) -> None:
        await sink.cancelled()

    async def cancel(self, connection: AgentConnection, run_id: str) -> bool:
        if not (connection.config or {}).get("cancel", True):
            return False
        event = self._cancel.get(run_id)
        if event is None:
            return False
        event.set()
        return True

    # Scheduler stand-in, stored in the data directory --------------------------------------------------
    @property
    def _jobs_path(self) -> Path:
        return self.domain.settings.data_dir / "fake-automations.json"

    def jobs(self) -> list[dict[str, Any]]:
        if not self._jobs_path.exists():
            return []
        return json.loads(self._jobs_path.read_text())

    def save_jobs(self, jobs: list[dict[str, Any]]) -> None:
        self._jobs_path.write_text(json.dumps(jobs, indent=2))

    async def automation(self, connection: AgentConnection, command: str, arguments: dict[str, Any]) -> dict[str, Any]:
        job_id = arguments.get("job_id")
        if job_id in self.unavailable_jobs:
            raise ProviderError("provider_unavailable", "The scheduler did not respond.")
        jobs = self.jobs()
        job = next((j for j in jobs if j["provider_job_id"] == job_id), None)
        if command == "list":
            return {"jobs": jobs}
        if job is None:
            raise ProviderError("not_found", "That job no longer exists.")
        if command in ("pause", "resume"):
            job["state"] = "paused" if command == "pause" else "active"
        elif command == "redirect":
            job["destination"] = arguments["destination"]
        elif command == "remove":
            jobs.remove(job)
        elif command == "run_now":
            job["last_outcome"] = "success"
            destination = job.get("destination") or ""
            space_id = destination.removeprefix("amsg:")
            await asyncio.to_thread(
                self.domain.post_agent_message,
                space_id,
                f"**{job['name']}** ran. All checks passed.",
                author=connection.name,
                meta={"automation_job_id": job_id},
            )
        self.save_jobs(jobs)
        return {"job": job if command != "remove" else None}
