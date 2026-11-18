"""M8 gate: deletion is a durable state machine that never strands a job or deletes the wrong one."""

from __future__ import annotations

import json
import time
import uuid

import pytest

from amsg import schemas
from amsg.db.models import AutomationLink
from amsg.errors import ApiError

from .conftest import Harness


def setup_jobs(h: Harness, count: int = 1) -> dict:
    space = h.post("/v1/spaces", json={"title": "Scheduled"}).json()
    jobs = [
        {
            "provider_job_id": f"job-{i}",
            "name": f"Job {i}",
            "schedule": "0 7 * * *",
            "timezone": "Europe/London",
            "next_run_at": "2026-11-07T20:00:00.000Z",
            "state": "active",
            "destination": f"amsg:{space['id']}",
        }
        for i in range(count)
    ]
    (h.settings.data_dir / "fake-automations.json").write_text(json.dumps(jobs))
    with h.domain.db.read() as session:
        connection = h.domain.primary_connection_id(session)
    h.domain.sync_automations(connection, [schemas.ConnectorJobReport.model_validate(j) for j in jobs])
    return h.get("/v1/spaces").json()[-1]


def provider(h: Harness):  # type: ignore[no-untyped-def]
    return h.client.app.state.services.runtime.providers.get("fake")


def start_delete(h: Harness, space: dict, policy: str):  # type: ignore[no-untyped-def]
    return h.post(
        f"/v1/spaces/{space['id']}/delete",
        json={"policy": policy, "expected_revision": space["revision"], "idempotency_key": str(uuid.uuid4())},
    )


def wait_operation(h: Harness, operation_id: str, *states: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        body = h.get(f"/v1/operations/{operation_id}").json()
        if body["state"] in states:
            return body
        time.sleep(0.05)
    raise AssertionError(body)


def test_automation_metadata_and_space_badge(h: Harness) -> None:
    space = setup_jobs(h)
    assert space["automation_count"] == 1
    job = h.get("/v1/automations").json()[0]
    assert job["space_id"] == space["id"]
    assert job["timezone"] == "Europe/London"
    assert job["schedule"] == "0 7 * * *"


def test_pause_resume_and_run_now_deliver_to_the_right_space(h: Harness) -> None:
    space = setup_jobs(h)
    job = h.get("/v1/automations").json()[0]
    assert h.post(f"/v1/automations/{job['id']}/actions", json={"action": "pause"}).json()["state"] == "paused"
    assert provider(h).jobs()[0]["state"] == "paused"
    assert h.post(f"/v1/automations/{job['id']}/actions", json={"action": "resume"}).json()["state"] == "active"
    assert h.post(f"/v1/automations/{job['id']}/actions", json={"action": "run_now"}).status_code == 200
    items = h.get(f"/v1/spaces/{space['id']}/messages").json()["items"]
    assert "ran" in items[-1]["markdown"]
    assert items[-1]["meta"]["automation_job_id"] == "job-0"


def test_stale_automation_revision_is_rejected(h: Harness) -> None:
    setup_jobs(h)
    job = h.get("/v1/automations").json()[0]
    h.post(f"/v1/automations/{job['id']}/actions", json={"action": "pause"})
    stale = h.post(
        f"/v1/automations/{job['id']}/actions", json={"action": "resume", "expected_revision": job["revision"]}
    )
    assert stale.status_code == 409


def test_plain_delete_refuses_a_linked_space(h: Harness) -> None:
    space = setup_jobs(h)
    response = start_delete(h, space, "delete")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "automation_linked"


def test_keep_and_move_redirects_then_deletes(h: Harness) -> None:
    space = setup_jobs(h, 2)
    operation = start_delete(h, space, "keep_and_move").json()
    done = wait_operation(h, operation["id"], "space_deleted", "failed")
    assert done["state"] == "space_deleted"
    assert all(job["destination"] == f"amsg:{h.general}" for job in provider(h).jobs())
    assert all(job["space_id"] == h.general for job in h.get("/v1/automations").json())
    assert space["id"] not in [s["id"] for s in h.get("/v1/spaces").json()]


def test_stop_and_delete_removes_jobs_from_the_provider(h: Harness) -> None:
    space = setup_jobs(h)
    operation = start_delete(h, space, "stop_and_delete").json()
    assert wait_operation(h, operation["id"], "space_deleted", "failed")["state"] == "space_deleted"
    assert provider(h).jobs() == []


def test_offline_scheduler_keeps_the_space_and_partial_retry_finishes(h: Harness) -> None:
    space = setup_jobs(h, 3)
    fake = provider(h)
    fake.unavailable_jobs = {"job-1"}
    operation = start_delete(h, space, "stop_and_delete").json()
    failed = wait_operation(h, operation["id"], "failed")
    assert failed["error_code"] == "provider_unavailable"
    assert [job["provider_job_id"] for job in fake.jobs()] == ["job-1"]
    kept = next(s for s in h.get("/v1/spaces").json() if s["id"] == space["id"])
    assert kept["pending_operation_id"] == operation["id"]
    assert sorted(failed["progress"]["jobs"].values()) == ["done", "done", "failed"]

    fake.unavailable_jobs = set()
    assert h.post(f"/v1/operations/{operation['id']}/retry").status_code == 202
    assert wait_operation(h, operation["id"], "space_deleted")["state"] == "space_deleted"
    assert fake.jobs() == []


def test_failed_deletion_can_be_dismissed(h: Harness) -> None:
    space = setup_jobs(h)
    fake = provider(h)
    fake.unavailable_jobs = {"job-0"}
    operation = start_delete(h, space, "stop_and_delete").json()
    wait_operation(h, operation["id"], "failed")
    assert h.post(f"/v1/operations/{operation['id']}/dismiss").status_code == 200
    kept = next(s for s in h.get("/v1/spaces").json() if s["id"] == space["id"])
    assert kept["pending_operation_id"] is None


def test_job_linked_during_deletion_stops_it(h: Harness) -> None:
    """A link that appears after the recheck must block deletion rather than strand the job."""
    space = setup_jobs(h)
    operation = start_delete(h, space, "stop_and_delete").json()
    wait_operation(h, operation["id"], "space_deleted", "failed")
    second = h.post("/v1/spaces", json={"title": "Second"}).json()
    report = schemas.ConnectorJobReport(provider_job_id="late", name="Late", destination=f"amsg:{second['id']}")
    with h.domain.db.read() as session:
        connection = h.domain.primary_connection_id(session)
    h.domain.sync_automations(connection, [report])
    held = h.domain.begin_delete(
        second["id"],
        schemas.DeletePolicy(policy="stop_and_delete", expected_revision=second["revision"], idempotency_key="k" * 16),
        "test",
    )
    with pytest.raises(ApiError) as caught:
        h.domain.finish_delete(held.id)
    assert caught.value.code == "automation_linked"
    assert second["id"] in [item.id for item in h.domain.list_spaces()]


def test_archive_keeps_the_destination(h: Harness) -> None:
    space = setup_jobs(h)
    archived = h.post(f"/v1/spaces/{space['id']}/archive").json()
    assert archived["archived_at"]
    assert h.get("/v1/automations").json()[0]["space_id"] == space["id"]
    assert space["id"] not in [s["id"] for s in h.get("/v1/spaces").json()]
    assert space["id"] in [s["id"] for s in h.get("/v1/spaces", params={"include_archived": True}).json()]
    assert h.post(f"/v1/spaces/{space['id']}/restore").json()["archived_at"] is None


def test_jobs_gone_from_the_provider_are_marked_removed(h: Harness) -> None:
    setup_jobs(h)
    with h.domain.db.read() as session:
        connection = h.domain.primary_connection_id(session)
    h.domain.sync_automations(connection, [])
    assert h.get("/v1/automations").json() == []


def test_stale_flag_when_never_synced_recently(h: Harness) -> None:
    setup_jobs(h)
    with h.domain.db.write() as session:
        session.query(AutomationLink).first().last_synced_at = "2020-01-01T00:00:00.000Z"
    assert h.get("/v1/automations").json()[0]["stale"] is True
