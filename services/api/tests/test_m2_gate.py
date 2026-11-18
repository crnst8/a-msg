"""M2 gate: durable, idempotent, revision-guarded state and event replay."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

import httpx
import pytest
import uvicorn
from sqlalchemy import select

from amsg import schemas
from amsg.api.app import create_app
from amsg.config import Settings
from amsg.db.models import DispatchJob, Event, Message, Run
from amsg.domain import Domain
from amsg.errors import ApiError

from .conftest import Harness, make_harness


def test_liveness_needs_no_auth_and_leaks_nothing(h: Harness) -> None:
    response = h.client.get("/health/live")
    assert response.json() == {"status": "ok"}
    assert h.client.get("/v1/health").status_code == 401


def test_error_envelope_shape(h: Harness) -> None:
    body = h.get("/v1/spaces/nope/messages").json()
    assert set(body["error"]) == {"code", "message", "retryable", "request_id", "details"}
    assert body["error"]["code"] == "not_found"


def test_repeated_submit_creates_one_message(h: Harness) -> None:
    client_id = str(uuid.uuid4())
    first = h.send(h.general, "hello", client_id)
    second = h.send(h.general, "hello", client_id)
    assert first.status_code == second.status_code == 202
    assert second.json()["duplicate"] is True
    assert first.json()["message"]["id"] == second.json()["message"]["id"]
    with h.domain.db.read() as session:
        assert len(list(session.scalars(select(Message).where(Message.role == "user")))) == 1


def test_changed_body_with_same_id_conflicts(h: Harness) -> None:
    client_id = str(uuid.uuid4())
    assert h.send(h.general, "one", client_id).status_code == 202
    response = h.send(h.general, "two", client_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_conflict"


def test_idempotency_key_with_changed_arguments_conflicts(h: Harness) -> None:
    body = {"title": "A", "idempotency_key": "key-00000001"}
    first = h.post("/v1/spaces", json=body)
    assert first.status_code == 201
    assert h.post("/v1/spaces", json=body).json()["id"] == first.json()["id"]
    assert h.post("/v1/spaces", json={**body, "title": "B"}).status_code == 409


def test_revoked_token_cannot_read_or_download(h: Harness) -> None:
    other = h.domain.issue_device_token("other")
    headers = {"Authorization": f"Bearer {other}"}
    upload = h.post("/v1/uploads", files={"file": ("a.txt", b"bytes", "text/plain")}).json()
    assert h.client.get("/v1/spaces", headers=headers).status_code == 200
    assert h.client.get(f"/v1/artifacts/{upload['id']}/content", headers=headers).status_code == 200
    devices = h.get("/v1/devices").json()
    target = next(d for d in devices if not d["current"])
    assert h.delete(f"/v1/devices/{target['id']}").status_code == 204
    assert h.client.get("/v1/spaces", headers=headers).status_code == 401
    assert h.client.get(f"/v1/artifacts/{upload['id']}/content", headers=headers).status_code == 401


def test_connector_token_cannot_use_device_routes(h: Harness) -> None:
    with h.domain.db.read() as session:
        connection = h.domain.primary_connection_id(session)
    token = h.domain.issue_connector_token(connection)
    assert h.client.get("/v1/spaces", headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_pairing_is_single_use_and_expires(h: Harness) -> None:
    code, _ = h.domain.issue_pairing_code()
    first = h.client.post("/v1/pair", json={"code": code, "device_name": "Mac"})
    assert first.status_code == 200
    token = first.json()["token"]
    assert h.client.get("/v1/spaces", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert h.client.post("/v1/pair", json={"code": code}).status_code == 401
    code2, _ = h.domain.issue_pairing_code()
    from amsg.db.models import PairingToken

    with h.domain.db.write() as session:
        for row in session.scalars(select(PairingToken)):
            row.expires_at = "2000-01-01T00:00:00.000Z"
    assert h.client.post("/v1/pair", json={"code": code2}).status_code == 401


def test_pairing_is_rate_limited(h: Harness) -> None:
    statuses = [h.client.post("/v1/pair", json={"code": "WRONG-CODE-0"}).status_code for _ in range(12)]
    assert statuses[-1] == 429


def test_stale_revision_fails(h: Harness) -> None:
    space = h.post("/v1/spaces", json={"title": "One"}).json()
    ok = h.patch(f"/v1/spaces/{space['id']}", json={"expected_revision": 1, "title": "Two"})
    assert ok.status_code == 200
    stale = h.patch(f"/v1/spaces/{space['id']}", json={"expected_revision": 1, "title": "Three"})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_revision"


def test_general_is_protected(h: Harness) -> None:
    general = h.get("/v1/spaces").json()[0]
    assert general["kind"] == "general"
    response = h.patch(f"/v1/spaces/{general['id']}", json={"expected_revision": 1, "title": "Other"})
    assert response.status_code == 409
    assert h.post(f"/v1/spaces/{general['id']}/archive").status_code == 409


def test_transaction_rollback_emits_no_event(h: Harness) -> None:
    domain = h.domain
    before = domain.snapshot().cursor
    with pytest.raises(RuntimeError), domain.db.write() as session:
        domain.emit(session, "space.created", None, {"x": 1})
        raise RuntimeError("boom")
    assert domain.snapshot().cursor == before
    with domain.db.read() as session:
        assert session.scalar(select(Event).where(Event.type == "space.created")) is None


def test_empty_transaction_wakes_nobody(h: Harness) -> None:
    woken = []
    unsubscribe = h.domain.notifier.subscribe(lambda: woken.append(1))
    with h.domain.db.write() as session:
        session.scalar(select(Event.seq).limit(1))
    assert woken == []
    h.post("/v1/spaces", json={"title": "Changes something"})
    assert woken
    unsubscribe()


def test_expired_cursor_requires_resync(h: Harness) -> None:
    h.send(h.general)
    h.domain.settings.event_retention_days = -1
    assert h.domain.prune_events() > 0
    response = h.client.get("/v1/events?after=0", headers=h.auth)
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "resync_required"
    future = h.client.get("/v1/events?after=99999999", headers=h.auth)
    assert future.status_code == 410


def test_accepted_queue_survives_restart(tmp_path: Path) -> None:
    domain = _seeded(tmp_path)
    with domain.db.read() as session:
        general = domain.general_space_id(session)
    from amsg import schemas

    receipt = domain.accept_message(general, schemas.MessageCreate(client_message_id=str(uuid.uuid4()), markdown="x"))
    domain.close()
    reopened = Domain(Settings(data_dir=tmp_path, provider="fake", dev_mode=True))
    with reopened.db.read() as session:
        job = session.scalar(select(DispatchJob).where(DispatchJob.run_id == receipt.run.id))
        assert job is not None and job.state == "queued"
    reopened.close()


def _seeded(path: Path) -> Domain:
    from amsg.db.migrate import upgrade

    settings = Settings(data_dir=path, provider="fake", dev_mode=True)
    settings.ensure_dirs()
    upgrade(settings.db_path, settings.backup_dir)
    domain = Domain(settings)
    domain.seed()
    return domain


def test_general_seeded_once(tmp_path: Path) -> None:
    domain = _seeded(tmp_path)
    domain.seed()
    assert len([s for s in domain.list_spaces() if s.kind == "general"]) == 1
    domain.close()


def test_pagination_bounds(h: Harness) -> None:
    space = h.post("/v1/spaces", json={"title": "Paging"}).json()["id"]
    domain = h.domain
    for index in range(12):
        domain.post_agent_message(space, f"m{index}", author="t")
    page = h.get(f"/v1/spaces/{space}/messages?limit=5").json()
    assert [m["markdown"] for m in page["items"]] == ["m7", "m8", "m9", "m10", "m11"]
    older = h.get(f"/v1/spaces/{space}/messages?limit=5&before={page['next_before']}").json()
    assert older["items"][-1]["markdown"] == "m6"
    assert h.get(f"/v1/spaces/{space}/messages?limit=500").status_code == 422


def test_json_body_limit(h: Harness) -> None:
    payload = {"client_message_id": str(uuid.uuid4()), "markdown": "x" * (3 * 1024 * 1024)}
    assert h.post(f"/v1/spaces/{h.general}/messages", json=payload).status_code == 413


def test_snapshot_is_consistent_with_its_cursor(h: Harness) -> None:
    h.post("/v1/spaces", json={"title": "Snap"})
    snap = h.get("/v1/snapshot").json()
    assert any(s["title"] == "Snap" for s in snap["spaces"])
    with h.domain.db.read() as session:
        top = session.scalar(select(Event.seq).order_by(Event.seq.desc()).limit(1))
    assert snap["cursor"] == top


def test_run_lifecycle_streams_to_completion(h: Harness) -> None:
    receipt = h.send(h.general, "health").json()
    assert receipt["message"]["status"] == "queued_server"
    assert h.wait_run(receipt["run"]["id"]) == "completed"
    items = h.get(f"/v1/spaces/{h.general}/messages").json()["items"]
    assert [m["role"] for m in items] == ["user", "agent"]
    assert items[0]["status"] == "completed"
    assert "Health sweep done" in items[1]["markdown"]
    assert items[1]["artifacts"][0]["display_name"].startswith("vps-health")


def test_one_active_run_per_space_keeps_order(h: Harness) -> None:
    first = h.send(h.general, "slow").json()
    second = h.send(h.general, "second").json()
    h.wait_run(first["run"]["id"], "running")
    time.sleep(0.2)
    with h.domain.db.read() as session:
        assert session.get(Run, second["run"]["id"]).state == "queued"
    assert h.post(f"/v1/runs/{first['run']['id']}/cancel").status_code == 202
    assert h.wait_run(first["run"]["id"]) == "cancelled"
    assert h.wait_run(second["run"]["id"]) == "completed"


def test_concurrency_bound_across_spaces(h: Harness) -> None:
    spaces = [h.post("/v1/spaces", json={"title": f"S{i}"}).json()["id"] for i in range(3)]
    runs = [h.send(s, "slow").json()["run"]["id"] for s in spaces]
    h.wait_run(runs[0], "running")
    h.wait_run(runs[1], "running")
    time.sleep(0.2)
    with h.domain.db.read() as session:
        assert session.get(Run, runs[2]).state == "queued"
    for run in runs:
        h.post(f"/v1/runs/{run}/cancel")
    for run in runs:
        h.wait_run(run)


def test_queue_edit_rejected_after_claim(h: Harness) -> None:
    first = h.send(h.general, "slow").json()
    queued = h.send(h.general, "queued text").json()["message"]
    h.wait_run(first["run"]["id"], "running")
    edited = h.patch(
        f"/v1/queued-messages/{queued['id']}", json={"expected_revision": queued["revision"], "markdown": "edited"}
    )
    assert edited.status_code == 200
    assert edited.json()["markdown"] == "edited"
    stale = h.patch(
        f"/v1/queued-messages/{queued['id']}", json={"expected_revision": queued["revision"], "markdown": "x"}
    )
    assert stale.status_code == 409
    assert h.delete(f"/v1/queued-messages/{queued['id']}").status_code == 204
    assert h.get(f"/v1/queued-messages/{queued['id']}").status_code == 404
    h.post(f"/v1/runs/{first['run']['id']}/cancel")
    h.wait_run(first["run"]["id"])
    done = h.patch(f"/v1/queued-messages/{first['message']['id']}", json={"expected_revision": 1, "markdown": "x"})
    assert done.status_code == 409
    assert done.json()["error"]["code"] == "already_dispatched"


def test_failed_run_keeps_partial_and_retry_is_labelled(h: Harness) -> None:
    run = h.send(h.general, "fail please").json()["run"]
    assert h.wait_run(run["id"]) == "failed"
    reply = h.get(f"/v1/spaces/{h.general}/messages").json()["items"][-1]
    assert reply["status"] == "failed"
    assert reply["markdown"].startswith("Starting the check")
    assert reply["error_code"] == "provider_error"
    retried = h.post(f"/v1/runs/{run['id']}/retry").json()
    assert retried["attempt"] == 2 and retried["attempt_of"] == run["id"]
    assert h.post(f"/v1/runs/{run['id']}/retry").json()["id"] == retried["id"]
    h.wait_run(retried["id"])


def test_unsupported_cancel_stays_requested(h: Harness) -> None:
    from amsg.db.models import AgentConnection

    with h.domain.db.write() as session:
        connection = session.get(AgentConnection, h.domain.primary_connection_id(session))
        connection.config = {"cancel": False}
    run = h.send(h.general, "health").json()["run"]
    h.wait_run(run["id"], "running")
    assert h.post(f"/v1/runs/{run['id']}/cancel").json()["state"] == "cancel_requested"
    assert h.wait_run(run["id"]) == "completed"


def test_restart_during_run_marks_interrupted_not_rerun(tmp_path: Path) -> None:
    domain = _seeded(tmp_path)
    from amsg import schemas

    with domain.db.read() as session:
        general = domain.general_space_id(session)
    domain.accept_message(general, schemas.MessageCreate(client_message_id=str(uuid.uuid4()), markdown="x"))
    job = domain.claim_message_job(None)
    domain.append_delta(job["run_id"], "partial")
    domain.close()
    harness = make_harness(tmp_path)
    h = next(harness)
    try:
        with h.domain.db.read() as session:
            run = session.get(Run, job["run_id"])
            reply = session.get(Message, run.reply_message_id)
            assert (run.state, run.error_code, reply.markdown) == ("failed", "interrupted", "partial")
            jobs = session.scalars(select(DispatchJob).where(DispatchJob.state == "queued")).all()
            assert jobs == []
    finally:
        harness.close()


def test_bridge_claim_without_ack_becomes_uncertain(tmp_path: Path) -> None:
    domain = _seeded(tmp_path)
    from amsg import schemas
    from amsg.db.models import AgentConnection

    with domain.db.write() as session:
        session.get(AgentConnection, domain.primary_connection_id(session)).kind = "hermes"
    with domain.db.read() as session:
        general = domain.general_space_id(session)
    domain.accept_message(general, schemas.MessageCreate(client_message_id=str(uuid.uuid4()), markdown="x"))
    job = domain.claim_message_job(None)
    domain.reconcile_after_restart(("fake",))
    with domain.db.read() as session:
        assert session.get(Run, job["run_id"]).state == "delivery_uncertain"
    domain.close()


def test_domain_error_is_not_a_crash(tmp_path: Path) -> None:
    domain = _seeded(tmp_path)
    from amsg import schemas

    with pytest.raises(ApiError):
        domain.accept_message("missing", schemas.MessageCreate(client_message_id=str(uuid.uuid4()), markdown="x"))
    domain.close()


def test_sse_replays_then_streams_live(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, provider="fake", dev_mode=True, port=18791)
    app = create_app(settings)
    domain = app.state.services.domain
    token = domain.issue_device_token("sse")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18791, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    headers = {"Authorization": f"Bearer {token}"}
    base = "http://127.0.0.1:18791"
    try:
        for _ in range(100):
            try:
                httpx.get(f"{base}/health/live")
                break
            except httpx.TransportError:
                time.sleep(0.05)
        with domain.db.read() as session:
            general = domain.general_space_id(session)
        httpx.post(
            f"{base}/v1/spaces/{general}/messages",
            json={"client_message_id": str(uuid.uuid4()), "markdown": "file"},
            headers=headers,
        )
        seen: list[dict] = []
        with httpx.stream("GET", f"{base}/v1/events?after=0", headers=headers, timeout=5) as stream:
            data = None
            for line in stream.iter_lines():
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    if "seq" in data:
                        seen.append(data)
                if data and data.get("type") == "run.completed":
                    break
        seqs = [row["seq"] for row in seen]
        assert seqs == sorted(set(seqs))
        kinds = {row["type"] for row in seen}
        assert {"message.accepted", "run.started", "run.delta", "artifact.created", "run.completed"} <= kinds
        revisions = [r["payload"]["revision"] for r in seen if r["type"] == "run.delta"]
        assert revisions == sorted(revisions)
    finally:
        app.state.stopping.set()
        server.should_exit = True
        thread.join(timeout=10)


def test_sse_event_committed_during_a_read_is_not_held_until_the_heartbeat(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, provider="fake", dev_mode=True, port=18792, heartbeat_seconds=30)
    app = create_app(settings)
    domain = app.state.services.domain
    token = domain.issue_device_token("sse")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18792, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = "http://127.0.0.1:18792"

    # Puts a commit in the gap the stream cannot see: after its read finished, before it starts waiting.
    read = domain.events_after
    fired = threading.Event()

    def read_then_commit(cursor: int):  # type: ignore[no-untyped-def]
        rows = read(cursor)
        if not rows and not fired.is_set():
            fired.set()
            domain.create_space(schemas.SpaceCreate(title="In the gap", idempotency_key=str(uuid.uuid4())), "user")
            time.sleep(0.1)
        return rows

    domain.events_after = read_then_commit
    try:
        for _ in range(100):
            try:
                httpx.get(f"{base}/health/live")
                break
            except httpx.TransportError:
                time.sleep(0.05)
        headers = {"Authorization": f"Bearer {token}"}
        cursor = httpx.get(f"{base}/v1/snapshot", headers=headers).json()["cursor"]
        with httpx.stream("GET", f"{base}/v1/events?after={cursor}", headers=headers, timeout=4) as stream:
            kinds: list[str] = []
            for line in stream.iter_lines():
                if line.startswith("event: "):
                    kinds.append(line[7:])
                if "space.created" in kinds and fired.is_set():
                    break
        assert "space.created" in kinds
    finally:
        domain.events_after = read
        app.state.stopping.set()
        server.should_exit = True
        thread.join(timeout=10)


def test_search_finds_messages_and_files(h: Harness) -> None:
    run = h.send(h.general, "health").json()["run"]
    h.wait_run(run["id"])
    hits = h.get("/v1/search", params={"q": "filesystem"}).json()["items"]
    assert any(hit["type"] == "message" for hit in hits)
    hits = h.get("/v1/search", params={"q": "vps-health"}).json()["items"]
    assert any(hit["type"] == "artifact" for hit in hits)
    assert h.get("/v1/search", params={"q": '"; DROP'}).status_code == 200
