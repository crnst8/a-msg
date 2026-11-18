# ruff: noqa: E501
"""M9 gate: coding sessions over structured protocols, truthful cancel, and guarded config patches."""

from __future__ import annotations

import hashlib
import json
import stat
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from amsg.providers.cli import build_argv, parse_claude, parse_codex

from .conftest import Harness, make_harness

FAKE_CODEX = """#!/usr/bin/env python3
import json, os, signal, sys, time
argv = sys.argv[1:]
print(json.dumps({"type": "thread.started", "thread_id": "thread-1"}), flush=True)
prompt = argv[-1]
os.makedirs(".amsg/outbox", exist_ok=True)
if "hang" in prompt:
    print(json.dumps({"type": "item.started", "item": {"type": "command_execution", "command": "sleep 60"}}), flush=True)
    signal.signal(signal.SIGTERM, lambda *_: (print(json.dumps({"type": "turn.failed", "error": {"message": "stopped"}}), flush=True), sys.exit(143)))
    time.sleep(60)
if "boom" in prompt:
    print(json.dumps({"type": "turn.failed", "error": {"message": "Model unavailable"}}), flush=True)
    sys.exit(1)
open("args.json", "w").write(json.dumps(argv))
open(".amsg/outbox/result.txt", "w").write("fixture output")
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Finished the **task**."}}), flush=True)
print(json.dumps({"type": "turn.completed", "usage": {}}), flush=True)
"""


# The interpreter running these tests exists on every platform; /bin/true does not exist on macOS.
RELOAD_OK = [sys.executable, "-c", "raise SystemExit(0)"]
RELOAD_FAILS = [sys.executable, "-c", "raise SystemExit(1)"]


@pytest.fixture
def code(tmp_path: Path):  # type: ignore[no-untyped-def]
    root = tmp_path / "work"
    root.mkdir()
    program = tmp_path / "fake-codex"
    program.write_text(FAKE_CODEX)
    program.chmod(program.stat().st_mode | stat.S_IEXEC)
    harness = make_harness(tmp_path / "data")
    h = next(harness)
    connection = h.post(
        "/v1/connections",
        json={"name": "Codex", "kind": "codex", "config": {"allowed_roots": [str(root)], "command": str(program)}},
    ).json()
    space = h.post(
        "/v1/spaces",
        json={
            "title": "Session",
            "kind": "code",
            "agent_connection_id": connection["id"],
            "working_directory": str(root),
        },
    ).json()
    yield h, connection, space, root
    harness.close()


def test_parsers_understand_both_protocols() -> None:
    assert parse_codex({"type": "thread.started", "thread_id": "t"}).session_id == "t"
    assert parse_codex({"type": "item.completed", "item": {"type": "agent_message", "text": "x"}}).text == "x"
    assert parse_codex({"type": "turn.completed"}).final
    assert parse_codex({"type": "turn.failed", "error": {"message": "bad"}}).error == "bad"
    assert parse_claude({"type": "system", "subtype": "init", "session_id": "s"}).session_id == "s"
    event = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
    assert parse_claude(event).text == "hi"
    tool = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]},
    }
    assert parse_claude(tool).activity == "Bash ls"
    assert parse_claude({"type": "result", "subtype": "success", "is_error": False}).final
    assert parse_claude({"type": "result", "is_error": True, "result": "no"}).error == "no"


def test_chat_text_is_one_argument_never_a_shell(code) -> None:  # type: ignore[no-untyped-def]
    h, _, space, root = code
    text = "fix; rm -rf / && echo $(whoami) `id` --sandbox danger-full-access"
    run = h.send(space["id"], text).json()["run"]
    assert h.wait_run(run["id"], timeout=10) == "completed"
    argv = json.loads((root / "args.json").read_text())
    assert argv[-1].endswith(text)
    assert argv[-2] == "--"
    assert "danger-full-access" not in " ".join(argv[:-1])


def test_session_completes_and_returns_a_file(code) -> None:  # type: ignore[no-untyped-def]
    h, _, space, root = code
    run = h.send(space["id"], "do the fixture task").json()["run"]
    assert h.wait_run(run["id"], timeout=10) == "completed"
    reply = h.get(f"/v1/spaces/{space['id']}/messages").json()["items"][-1]
    assert reply["markdown"] == "Finished the **task**."
    assert reply["author"] == "Codex"
    assert [a["display_name"] for a in reply["artifacts"]] == ["result.txt"]
    assert h.get(f"/v1/artifacts/{reply['artifacts'][0]['id']}/content").content == b"fixture output"
    assert not list((root / ".amsg" / "outbox").iterdir())
    binding = h.get("/v1/spaces").json()
    assert next(s for s in binding if s["id"] == space["id"])["session"]["provider_session_id"] == "thread-1"


def test_second_turn_resumes_the_session(code) -> None:  # type: ignore[no-untyped-def]
    h, _, space, root = code
    h.wait_run(h.send(space["id"], "first").json()["run"]["id"], timeout=10)
    h.wait_run(h.send(space["id"], "second").json()["run"]["id"], timeout=10)
    argv = json.loads((root / "args.json").read_text())
    assert argv[:3] == ["exec", "resume", "thread-1"]


def test_cancellation_is_truthful(code) -> None:  # type: ignore[no-untyped-def]
    h, _, space, _ = code
    run = h.send(space["id"], "hang").json()["run"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        items = h.get(f"/v1/spaces/{space['id']}/messages").json()["items"]
        if items[-1]["activity"]:
            break
        time.sleep(0.05)
    assert items[-1]["activity"] == "Running sleep 60"
    assert h.post(f"/v1/runs/{run['id']}/cancel").json()["state"] == "cancel_requested"
    assert h.wait_run(run["id"], timeout=10) == "cancelled"


def test_provider_failure_is_reported(code) -> None:  # type: ignore[no-untyped-def]
    h, _, space, _ = code
    run = h.send(space["id"], "boom").json()["run"]
    assert h.wait_run(run["id"], timeout=10) == "failed"
    reply = h.get(f"/v1/spaces/{space['id']}/messages").json()["items"][-1]
    assert reply["error_message"] == "Model unavailable"


def test_directory_must_be_inside_allowed_roots(code, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    h, connection, _, _ = code
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    body = {"title": "x", "kind": "code", "agent_connection_id": connection["id"], "working_directory": str(outside)}
    assert h.post("/v1/spaces", json=body).status_code == 422


def test_session_requires_a_directory(code) -> None:  # type: ignore[no-untyped-def]
    h, connection, _, _ = code
    response = h.post("/v1/spaces", json={"title": "x", "kind": "code", "agent_connection_id": connection["id"]})
    assert response.json()["error"]["code"] == "directory_required"


def test_secrets_are_not_returned(h: Harness) -> None:
    response = h.post(
        "/v1/connections",
        json={"name": "C", "kind": "codex", "config": {}, "secrets": {"api_key": "sk-test-value"}},
    )
    assert "sk-test-value" not in response.text
    assert response.json()["has_secret"] is True
    assert "sk-test-value" not in h.get("/v1/connections").text


def test_access_maps_to_each_agent_flag() -> None:
    codex = type("C", (), {"kind": "codex", "config": {}})()
    claude = type("C", (), {"kind": "claude", "config": {}})()
    assert build_argv(codex, "x", "/tmp/x", None)[2:4] == ["--sandbox", "workspace-write"]
    assert build_argv(codex, "x", "/tmp/x", None, "read")[2:4] == ["--sandbox", "read-only"]
    assert build_argv(codex, "x", "/tmp/x", None, "full")[2:4] == ["--sandbox", "danger-full-access"]
    assert build_argv(codex, "x", "/tmp/x", "t", "full")[2:6] == [
        "resume",
        "t",
        "-c",
        'sandbox_mode="danger-full-access"',
    ]
    assert "bypassPermissions" in build_argv(claude, "x", "/tmp/x", None, "full")
    assert "plan" in build_argv(claude, "x", "/tmp/x", None, "read")


def test_session_access_is_chosen_changed_and_capped(code) -> None:  # type: ignore[no-untyped-def]
    h, connection, space, root = code
    assert space["session"]["access"] is None
    patched = h.patch(f"/v1/spaces/{space['id']}", json={"expected_revision": space["revision"], "access": "full"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["session"]["access"] == "full"
    assert h.wait_run(h.send(space["id"], "go").json()["run"]["id"], timeout=10) == "completed"
    assert json.loads((root / "args.json").read_text())[:3] == ["exec", "--sandbox", "danger-full-access"]
    capped = h.post(
        "/v1/connections",
        json={"name": "Capped", "kind": "codex", "config": {"allowed_roots": [str(root)], "max_access": "workspace"}},
    ).json()
    body = {
        "title": "x",
        "kind": "code",
        "agent_connection_id": capped["id"],
        "working_directory": str(root),
        "access": "full",
    }
    refused = h.post("/v1/spaces", json=body)
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "access_not_allowed"
    thread = h.post("/v1/spaces", json={"title": "Thread"}).json()
    response = h.patch(f"/v1/spaces/{thread['id']}", json={"expected_revision": thread["revision"], "access": "read"})
    assert response.status_code == 422


def test_argv_uses_a_separator_for_both_agents() -> None:
    connection = type("C", (), {"kind": "claude", "config": {}})()
    argv = build_argv(connection, "--help", "/tmp/x", None)
    assert argv[-2:] == ["--", "--help"]


@pytest.fixture
def config(tmp_path: Path, h: Harness):  # type: ignore[no-untyped-def]
    path = tmp_path / "config.yaml"
    path.write_bytes(b"model: one\n# keep this comment\n")
    connection = h.post(
        "/v1/connections",
        json={"name": "Agent", "kind": "bridge", "config": {"config_path": str(path), "reload_command": RELOAD_OK}},
    ).json()
    return h, connection, path


def propose(h: Harness, connection: dict, path: Path, proposed: str, base: str | None = None):  # type: ignore[no-untyped-def]
    checksum = base or hashlib.sha256(path.read_bytes()).hexdigest()
    return h.post(
        f"/v1/connections/{connection['id']}/config-patches",
        json={"path": str(path), "proposed": proposed, "base_checksum": checksum},
    )


def test_patch_applies_and_rollback_restores_exact_bytes(config) -> None:  # type: ignore[no-untyped-def]
    h, connection, path = config
    original = path.read_bytes()
    patch = propose(h, connection, path, "model: two\n").json()
    assert "-model: one" in patch["diff"] and "+model: two" in patch["diff"]
    assert path.read_bytes() == original
    applied = h.post(f"/v1/config-patches/{patch['id']}/apply").json()
    assert applied["state"] == "applied"
    assert path.read_text() == "model: two\n"
    restored = h.post(f"/v1/config-patches/{patch['id']}/rollback").json()
    assert restored["state"] == "rolled_back"
    assert path.read_bytes() == original


def test_stale_patch_cannot_overwrite_newer_configuration(config) -> None:  # type: ignore[no-untyped-def]
    h, connection, path = config
    patch = propose(h, connection, path, "model: two\n").json()
    path.write_bytes(b"model: edited elsewhere\n")
    response = h.post(f"/v1/config-patches/{patch['id']}/apply")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale_base"
    assert path.read_bytes() == b"model: edited elsewhere\n"
    assert propose(h, connection, path, "x: 1\n", base="0" * 64).status_code == 409


def test_invalid_yaml_is_refused(config) -> None:  # type: ignore[no-untyped-def]
    h, connection, path = config
    assert propose(h, connection, path, "a: [1, 2\n").json()["error"]["code"] == "config_syntax"


def test_unhealthy_patch_offers_rollback(config) -> None:  # type: ignore[no-untyped-def]
    h, connection, path = config
    with h.domain.db.write() as session:
        from amsg.db.models import AgentConnection

        row = session.get(AgentConnection, connection["id"])
        row.config = {**row.config, "reload_command": RELOAD_FAILS}
    original = path.read_bytes()
    patch = propose(h, connection, path, "model: broken\n").json()
    assert h.post(f"/v1/config-patches/{patch['id']}/apply").json()["state"] == "unhealthy"
    assert h.post(f"/v1/config-patches/{patch['id']}/rollback").json()["state"] == "rolled_back"
    assert path.read_bytes() == original


def test_missing_reload_command_is_unhealthy_and_restorable(config) -> None:  # type: ignore[no-untyped-def]
    h, connection, path = config
    with h.domain.db.write() as session:
        from amsg.db.models import AgentConnection

        row = session.get(AgentConnection, connection["id"])
        row.config = {**row.config, "reload_command": ["/nonexistent/hermes-reload"]}
    original = path.read_bytes()
    patch = propose(h, connection, path, "model: two\n").json()
    assert h.post(f"/v1/config-patches/{patch['id']}/apply").json()["state"] == "unhealthy"
    assert h.post(f"/v1/config-patches/{patch['id']}/rollback").json()["state"] == "rolled_back"
    assert path.read_bytes() == original


def test_agents_cannot_apply_their_own_patch(config) -> None:  # type: ignore[no-untyped-def]
    h, connection, path = config
    patch = propose(h, connection, path, "model: two\n").json()
    token = h.domain.issue_connector_token(connection["id"])
    client: TestClient = h.client
    response = client.post(f"/v1/config-patches/{patch['id']}/apply", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert path.read_bytes().startswith(b"model: one")
