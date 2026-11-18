# ruff: noqa: E501
"""Remote coding agents: a runner on one host working for a service on another, over the real connector routes."""

from __future__ import annotations

import asyncio
import json
import socket
import stat
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from amsg.api.app import create_app
from amsg.config import Settings
from amsg.runner import Runner

from .test_m9_code import FAKE_CODEX

LOGIN = """argv = sys.argv[1:]
if argv == ["--version"]:
    print("codex-cli 9.9.9"); sys.exit(0)
if argv == ["login", "status"]:
    print("Logged in using ChatGPT"); sys.exit(0)
"""


class Rig:
    def __init__(self, base: str, device: dict[str, str], domain: Any, root: Path, program: Path) -> None:
        self.base, self.device, self.domain, self.root, self.program = base, device, domain, root, program
        self.connection = ""
        self.runner_token = ""
        self.runner: Runner | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None

    def call(self, method: str, path: str, **options: Any) -> httpx.Response:
        return httpx.request(method, f"{self.base}{path}", headers=self.device, timeout=30, **options)

    def start(self, token: str, **config: Any) -> None:
        ready = threading.Event()

        def run() -> None:
            async def main() -> None:
                self.loop = asyncio.get_running_loop()
                self.runner = Runner(
                    self.base, token, agent="codex", roots=[self.root], config={"command": str(self.program), **config}
                )
                ready.set()
                await self.runner.serve()

            asyncio.run(main())

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        assert ready.wait(5)

    def stop(self) -> None:
        if self.runner and self.thread and self.thread.is_alive():
            assert self.loop
            self.loop.call_soon_threadsafe(self.runner.stopping.set)
            self.thread.join(15)

    def wait_connected(self) -> dict[str, Any]:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            row = next(c for c in self.call("GET", "/v1/connections").json() if c["id"] == self.connection)
            if row["state"] == "connected" and row["config"].get("allowed_roots"):
                return row
            time.sleep(0.02)
        raise AssertionError("runner never connected")

    def session(self, directory: str | Path, **extra: Any) -> dict[str, Any]:
        response = self.call(
            "POST",
            "/v1/spaces",
            json={
                "title": "Remote",
                "kind": "code",
                "agent_connection_id": self.connection,
                "working_directory": str(directory),
                **extra,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    def send(self, space: dict[str, Any], text: str, artifacts: list[str] | None = None) -> str:
        body = {"client_message_id": str(uuid.uuid4()), "markdown": text, "artifact_ids": artifacts or []}
        response = self.call("POST", f"/v1/spaces/{space['id']}/messages", json=body)
        assert response.status_code == 202, response.text
        return response.json()["run"]["id"]

    def wait_run(self, run_id: str, timeout: float = 15) -> str:
        from amsg.db.models import Run

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.domain.db.read() as session:
                state = session.get(Run, run_id).state
            if state in ("completed", "failed", "cancelled"):
                return state
            time.sleep(0.02)
        raise AssertionError(f"run stayed {state}")

    def reply(self, space: dict[str, Any]) -> dict[str, Any]:
        return self.call("GET", f"/v1/spaces/{space['id']}/messages").json()["items"][-1]


@pytest.fixture
def rig(tmp_path: Path):  # type: ignore[no-untyped-def]
    root = tmp_path / "work"
    root.mkdir()
    program = tmp_path / "fake-codex"
    program.write_text(FAKE_CODEX.replace("argv = sys.argv[1:]\n", LOGIN))
    program.chmod(program.stat().st_mode | stat.S_IEXEC)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = Settings(data_dir=tmp_path / "service", provider="fake", dev_mode=True, port=port)
    app = create_app(settings)
    domain = app.state.services.domain
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{base}/health/live")
            break
        except httpx.TransportError:
            time.sleep(0.05)
    device = {"Authorization": f"Bearer {domain.issue_device_token('t')}"}
    harness = Rig(base, device, domain, root, program)
    token = domain.issue_runner_token("Codex on test")
    harness.connection = next(c.id for c in domain.list_connections() if c.name == "Codex on test")
    harness.runner_token = token
    harness.start(token)
    harness.wait_connected()
    yield harness
    harness.stop()
    server.should_exit = True
    thread.join(10)


def test_runner_registers_what_it_runs(rig: Rig) -> None:
    row = rig.wait_connected()
    assert row["kind"] == "bridge"
    assert row["capabilities"]["agent"] == "codex"
    assert row["config"]["allowed_roots"] == [str(rig.root.resolve())]


def test_test_connection_reports_the_program_and_login(rig: Rig) -> None:
    result = rig.call("POST", f"/v1/connections/{rig.connection}/test").json()
    details = [check["detail"] for check in result["checks"]]
    assert result["ok"] is True
    assert "codex-cli 9.9.9" in details
    assert "Logged in with ChatGPT" in details
    assert rig.wait_connected()["capabilities"]["agent"] == "codex"


def test_session_runs_on_the_runner_and_returns_a_file(rig: Rig) -> None:
    space = rig.session(rig.root)
    assert rig.wait_run(rig.send(space, "do the fixture task")) == "completed"
    reply = rig.reply(space)
    assert reply["markdown"] == "Finished the **task**."
    assert reply["author"] == "Codex on test"
    assert [a["display_name"] for a in reply["artifacts"]] == ["result.txt"]
    content = rig.call("GET", f"/v1/artifacts/{reply['artifacts'][0]['id']}/content").content
    assert content == b"fixture output"


def test_second_turn_resumes_inside_the_same_sandbox(rig: Rig) -> None:
    space = rig.session(rig.root)
    assert rig.wait_run(rig.send(space, "first")) == "completed"
    assert rig.wait_run(rig.send(space, "second")) == "completed"
    argv = json.loads((rig.root / "args.json").read_text())
    assert argv[:5] == ["exec", "resume", "thread-1", "-c", 'sandbox_mode="workspace-write"']


def test_folder_missing_on_the_service_host_is_accepted_then_checked_by_the_runner(rig: Rig) -> None:
    space = rig.session(rig.root / "not-here")
    assert rig.wait_run(rig.send(space, "go")) == "failed"
    assert rig.reply(space)["error_code"] == "directory_missing"


def test_service_refuses_a_folder_outside_the_reported_roots(rig: Rig) -> None:
    for folder in ("/etc", f"{rig.root}/../..", "relative/path"):
        response = rig.call(
            "POST",
            "/v1/spaces",
            json={"title": "x", "kind": "code", "agent_connection_id": rig.connection, "working_directory": folder},
        )
        assert response.status_code == 422, folder
        assert response.json()["error"]["code"] == "directory_not_allowed"


def test_runner_refuses_a_folder_the_service_wrongly_allows(rig: Rig, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    rig.domain.set_connection_config(rig.connection, allowed_roots=[str(tmp_path)])
    space = rig.session(other)
    assert rig.wait_run(rig.send(space, "go")) == "failed"
    assert rig.reply(space)["error_code"] == "directory_not_allowed"
    assert not (other / "args.json").exists()


def test_stop_ends_the_remote_process(rig: Rig) -> None:
    space = rig.session(rig.root)
    run = rig.send(space, "hang")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and rig.reply(space)["activity"] != "Running sleep 60":
        time.sleep(0.05)
    assert rig.reply(space)["activity"] == "Running sleep 60"
    assert rig.call("POST", f"/v1/runs/{run}/cancel").json()["state"] == "cancel_requested"
    assert rig.wait_run(run) == "cancelled"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and rig.runner and rig.runner.runs:
        time.sleep(0.02)
    assert rig.runner and rig.runner.runs == {}


def test_agent_failure_is_reported(rig: Rig) -> None:
    space = rig.session(rig.root)
    assert rig.wait_run(rig.send(space, "boom")) == "failed"
    assert rig.reply(space)["error_message"] == "Model unavailable"


def test_attachment_reaches_the_working_directory(rig: Rig) -> None:
    space = rig.session(rig.root)
    upload = rig.call("POST", "/v1/uploads", files={"file": ("notes.txt", b"from the app")})
    assert upload.status_code == 201, upload.text
    assert rig.wait_run(rig.send(space, "read it", [upload.json()["id"]])) == "completed"
    prompt = json.loads((rig.root / "args.json").read_text())[-1]
    relative = next(line[2:] for line in prompt.splitlines() if line.startswith("- .amsg/inbox/"))
    assert (rig.root / relative).read_bytes() == b"from the app"
    assert (rig.root / relative).resolve().is_relative_to(rig.root.resolve())


def test_stopping_the_runner_reports_the_run_as_interrupted(rig: Rig) -> None:
    space = rig.session(rig.root)
    run = rig.send(space, "hang")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and rig.reply(space)["activity"] != "Running sleep 60":
        time.sleep(0.05)
    rig.stop()
    assert rig.wait_run(run) == "failed"
    assert rig.reply(space)["error_code"] == "interrupted"


def test_new_credential_replaces_the_old_one_and_reuses_the_connection(rig: Rig) -> None:
    first = rig.domain.issue_runner_token("Other host")
    second = rig.domain.issue_runner_token("Other host")
    assert sum(c.name == "Other host" for c in rig.domain.list_connections()) == 1
    url = f"{rig.base}/v1/connector/dispatch"
    assert httpx.get(url, params={"wait": 0}, headers={"Authorization": f"Bearer {first}"}).status_code == 401
    assert httpx.get(url, params={"wait": 0}, headers={"Authorization": f"Bearer {second}"}).status_code == 200


def test_runner_allows_only_its_own_default_access_unless_raised(rig: Rig) -> None:
    capabilities = rig.wait_connected()["capabilities"]
    assert (capabilities["default_access"], capabilities["max_access"]) == ("workspace", "workspace")
    body = {
        "title": "x",
        "kind": "code",
        "agent_connection_id": rig.connection,
        "working_directory": str(rig.root),
        "access": "full",
    }
    response = rig.call("POST", "/v1/spaces", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "access_not_allowed"
    space = rig.session(rig.root, access="read")
    assert rig.wait_run(rig.send(space, "go")) == "completed"
    assert json.loads((rig.root / "args.json").read_text())[:3] == ["exec", "--sandbox", "read-only"]


def test_full_access_passes_through_when_the_runner_allows_it(rig: Rig) -> None:
    rig.stop()
    rig.start(rig.runner_token, max_access="full")
    deadline = time.monotonic() + 10
    while rig.wait_connected()["capabilities"].get("max_access") != "full":
        assert time.monotonic() < deadline
        time.sleep(0.02)
    space = rig.session(rig.root, access="full")
    assert space["session"]["access"] == "full"
    assert rig.wait_run(rig.send(space, "first")) == "completed"
    assert json.loads((rig.root / "args.json").read_text())[:3] == ["exec", "--sandbox", "danger-full-access"]
    patched = rig.call(
        "PATCH", f"/v1/spaces/{space['id']}", json={"expected_revision": space["revision"], "access": "workspace"}
    )
    assert patched.status_code == 200, patched.text
    assert rig.wait_run(rig.send(space, "second")) == "completed"
    argv = json.loads((rig.root / "args.json").read_text())
    assert argv[:5] == ["exec", "resume", "thread-1", "-c", 'sandbox_mode="workspace-write"']


def test_runner_refuses_access_the_service_wrongly_allows(rig: Rig) -> None:
    from amsg.db.models import SessionBinding

    space = rig.session(rig.root)
    with rig.domain.db.write() as session:
        session.query(SessionBinding).filter_by(space_id=space["id"]).update({"access": "full"})
    assert rig.wait_run(rig.send(space, "go")) == "failed"
    assert rig.reply(space)["error_code"] == "access_not_allowed"
    assert not (rig.root / "args.json").exists()
