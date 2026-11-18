"""Development fixtures. Reachable only from `amsg dev-seed`, which refuses to run without --dev."""

from __future__ import annotations

import base64
import uuid

from . import schemas
from .domain import Domain
from .providers.fake import HEALTH, TABLE
from .workers.runtime import Runtime

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAIAAAACUFjqAAAAFklEQVR4nGO8e/cuA27AxIAXjFRpAKAoAuUJ3mYnAAAAAElFTkSuQmCC"
)


def cid() -> str:
    return str(uuid.uuid4())


def seed(domain: Domain) -> None:
    """Build the conversations the design fixtures need, through the same domain paths as real use."""
    with domain.db.read() as session:
        general = domain.general_space_id(session)
        primary = domain.primary_connection_id(session)
    assert primary
    if domain.list_messages(general, before=None, limit=1).items:
        return

    def ask(space_id: str, text: str, reply: str, *, files: list[tuple[str, bytes]] | None = None) -> None:
        receipt = domain.accept_message(space_id, schemas.MessageCreate(client_message_id=cid(), markdown=text))
        job = domain.claim_message_job([primary])
        assert job and job["run_id"] == receipt.run.id
        domain.append_delta(job["run_id"], reply)
        ids = [
            domain.register_bytes(data, name, space_id=space_id, message_id=job["reply_message_id"]).id
            for name, data in files or []
        ]
        domain.complete_run(job["run_id"], artifact_ids=ids)

    ask(
        general,
        "Check on the health of all my servers.",
        HEALTH,
        files=[("vps-health-2026-11-05.md", HEALTH.encode())],
    )

    def thread(title: str, icon: str) -> str:
        return domain.create_space(
            schemas.SpaceCreate(title=title, icon=schemas.Icon(kind="catalog", value=icon)), "dev"
        )[0].id

    holiday = thread("Holiday planning", "umbrella")
    servers = thread("Server maintenance", "server")
    digest = thread("Morning digest", "newspaper")
    ask(
        holiday,
        "Shortlist three quiet places on the south coast for the second week of January.",
        "### Three quiet options\n\n1. **Bermagui** — a working harbour, ten minutes from Mimosa Rocks.\n"
        "2. **Narooma** — the inlet is sheltered when the southerly comes in.\n"
        "3. **Merimbula** — the most to do, and the easiest flight.\n\n"
        "> Bermagui fits *quiet* best. School holidays run to the end of January, so book soon.",
    )
    ask(
        servers,
        "Which volumes on media-1 are growing fastest?",
        TABLE,
        files=[("ha-disk.png", PNG), ("rotate-logs.sh", b"#!/bin/sh\njournalctl --vacuum-size=500M\n")],
    )
    ask(
        digest,
        "Send me the overnight summary at 7 each morning.",
        "Done. The digest will arrive here at 07:00 London time.",
    )
    fake_jobs = [
        {
            "provider_job_id": "digest-0700",
            "name": "Morning digest",
            "schedule": "0 7 * * *",
            "timezone": "Europe/London",
            "next_run_at": "2026-11-08T07:00:00.000Z",
            "last_run_at": "2026-11-07T07:00:00.000Z",
            "last_outcome": "success",
            "state": "active",
            "destination": f"amsg:{digest}",
        },
        {
            "provider_job_id": "weekly-health",
            "name": "Weekly health sweep",
            "schedule": "30 4 * * 1",
            "timezone": "Europe/London",
            "next_run_at": "2026-11-09T04:30:00.000Z",
            "last_run_at": "2026-11-02T04:30:00.000Z",
            "last_outcome": "success",
            "state": "active",
            "destination": f"amsg:{servers}",
        },
    ]
    (domain.settings.data_dir / "fake-automations.json").write_text(__import__("json").dumps(fake_jobs))
    domain.sync_automations(primary, [schemas.ConnectorJobReport.model_validate(job) for job in fake_jobs])
    domain.mark_read(general)
    domain.mark_read(holiday)
    domain.mark_read(digest)


__all__ = ["Runtime", "seed"]
