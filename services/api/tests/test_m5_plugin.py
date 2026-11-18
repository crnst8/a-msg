"""M5, service side: the plugin against the real a-msg API, with Hermes stubbed from verified signatures."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import uvicorn

from amsg.api.app import create_app
from amsg.config import Settings

ROOT = Path(__file__).resolve().parents[3]
PORT = 18793
BASE = f"http://127.0.0.1:{PORT}"


@dataclass
class Service:
    domain: object
    device: dict[str, str]
    connection: str
    token: str
    general: str
    app: object


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Service]:
    settings = Settings(data_dir=tmp_path, provider="hermes", port=PORT, workspace_roots=[tmp_path])
    app = create_app(settings)
    domain = app.state.services.domain
    with domain.db.read() as session:
        connection = domain.primary_connection_id(session)
        general = domain.general_space_id(session)
    token = domain.issue_connector_token(connection)
    device = {"Authorization": f"Bearer {domain.issue_device_token('t')}"}
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            httpx.get(f"{BASE}/health/live")
            break
        except httpx.TransportError:
            time.sleep(0.05)
    monkeypatch.setenv("AMSG_URL", BASE)
    monkeypatch.setenv("AMSG_CONNECTOR_TOKEN", token)
    monkeypatch.syspath_prepend(str(ROOT / "integrations" / "hermes"))
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "hermes_stub"))
    for name in [m for m in sys.modules if m.startswith(("amsg_plugin", "gateway", "tools"))]:
        del sys.modules[name]
    yield Service(domain, device, connection, token, general, app)
    app.state.stopping.set()
    server.should_exit = True
    thread.join(timeout=10)


class FakeGateway:
    """Runs the part of the Hermes gateway the adapter sees: a handler that sends, edits and finishes."""

    def __init__(self, reply: str = "**Done.**") -> None:
        adapter_module = importlib.import_module("amsg_plugin.adapter")
        self.event_mod = importlib.import_module("gateway.platforms.event")
        self.adapter = adapter_module.AmsgAdapter(importlib.import_module("gateway.config").PlatformConfig())
        self.adapter.on_event = self.on_event
        self.reply = reply
        self.seen: list = []
        self.outcome = self.event_mod.ProcessingOutcome.SUCCESS
        self.hang: asyncio.Event | None = None
        self.during = None

    async def on_event(self, event) -> None:
        self.seen.append(event)
        asyncio.create_task(self.process(event))

    async def process(self, event) -> None:
        if event.text == "/stop":
            if self.hang:
                self.hang.set()
            return
        sent = await self.adapter.send(event.source.chat_id, "**Do")
        await self.adapter.edit_message(event.source.chat_id, sent.message_id, self.reply)
        if self.during:
            await self.during(event)
        if self.hang:
            await self.hang.wait()
            self.outcome = self.event_mod.ProcessingOutcome.CANCELLED
        await self.adapter.on_processing_complete(event, self.outcome)


def message(service: Service, text: str, space: str | None = None) -> dict:
    response = httpx.post(
        f"{BASE}/v1/spaces/{space or service.general}/messages",
        headers=service.device,
        json={"client_message_id": str(uuid.uuid4()), "markdown": text},
    )
    assert response.status_code == 202
    return response.json()


def transcript(service: Service, space: str | None = None) -> list[dict]:
    return httpx.get(f"{BASE}/v1/spaces/{space or service.general}/messages", headers=service.device).json()["items"]


async def until(check, timeout: float = 5.0):  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await asyncio.to_thread(check)
        if value:
            return value
        await asyncio.sleep(0.03)
    raise AssertionError("condition not met")


def run(coroutine) -> None:  # type: ignore[no-untyped-def]
    asyncio.run(asyncio.wait_for(coroutine, timeout=20))


def test_message_round_trip_and_restart(service: Service) -> None:
    async def scenario() -> None:
        gateway = FakeGateway()
        assert await gateway.adapter.connect()
        message(service, "hello")
        replies = await until(
            lambda: [m for m in transcript(service) if m["role"] == "agent" and m["status"] == "completed"]
        )
        assert replies[0]["markdown"] == "**Done.**"
        event = gateway.seen[0]
        assert event.source.chat_id == service.general
        assert event.user_id == "amsg-owner"
        assert "a-msg space: General" in event.channel_prompt
        await gateway.adapter.disconnect()

        restarted = FakeGateway("After restart")
        assert await restarted.adapter.connect()
        message(service, "again")
        await until(lambda: any(m["markdown"] == "After restart" for m in transcript(service)))
        assert restarted.seen[0].source.chat_id == service.general
        await restarted.adapter.disconnect()

    run(scenario())


def test_connection_state_and_capabilities_are_reported(service: Service) -> None:
    async def scenario() -> None:
        gateway = FakeGateway()
        assert await gateway.adapter.connect()
        connections = httpx.get(f"{BASE}/v1/connections", headers=service.device).json()
        assert connections[0]["state"] == "connected"
        assert connections[0]["capabilities"]["streaming_mode"] == "edit"
        test = await asyncio.to_thread(
            httpx.post, f"{BASE}/v1/connections/{service.connection}/test", headers=service.device, timeout=30
        )
        assert test.json()["ok"], test.text
        assert {c["name"] for c in test.json()["checks"]} >= {"connector", "routing", "formats"}
        await gateway.adapter.disconnect()

    run(scenario())


def test_idle_connector_poll_does_not_spin(service: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    """A waiting poll must sleep. It once woke itself on its own empty commits and used a whole core."""
    from amsg.api import connector

    calls = []
    original = connector._next_job

    def counted(app, connection_id):  # type: ignore[no-untyped-def]
        calls.append(1)
        return original(app, connection_id)

    monkeypatch.setattr(connector, "_next_job", counted)
    headers = {"Authorization": f"Bearer {service.token}"}
    assert httpx.get(f"{BASE}/v1/connector/dispatch?wait=1.5", headers=headers, timeout=10).json() is None
    assert len(calls) <= 3


def test_failure_keeps_partial_reply(service: Service) -> None:
    async def scenario() -> None:
        gateway = FakeGateway("Partial")
        gateway.outcome = gateway.event_mod.ProcessingOutcome.FAILURE
        assert await gateway.adapter.connect()
        message(service, "x")
        reply = await until(
            lambda: next((m for m in transcript(service) if m["role"] == "agent" and m["status"] == "failed"), None)
        )
        assert reply["markdown"] == "Partial"
        await gateway.adapter.disconnect()

    run(scenario())


def test_streaming_cursor_never_reaches_the_transcript(service: Service) -> None:
    async def scenario() -> None:
        gateway = FakeGateway("Stopped mid-word ▉")
        gateway.outcome = gateway.event_mod.ProcessingOutcome.FAILURE

        async def next_segment(event) -> None:  # type: ignore[no-untyped-def]
            await gateway.adapter.send(event.source.chat_id, "After a tool ▉")

        gateway.during = next_segment
        assert await gateway.adapter.connect()
        message(service, "x")
        reply = await until(
            lambda: next((m for m in transcript(service) if m["role"] == "agent" and m["status"] == "failed"), None)
        )
        assert reply["markdown"] == "Stopped mid-word\n\nAfter a tool"
        await gateway.adapter.disconnect()

    run(scenario())


@pytest.mark.parametrize(
    ("frame", "shown"),
    [
        ("Plain text ▉", "Plain text"),
        ("The hostname is ` ▉`", "The hostname is "),
        ("The hostname is `vps ▉`", "The hostname is `vps`"),
        ("Some **bold ▉**", "Some **bold**"),
        ("Some ** ▉**", "Some "),
        ("```py\nprint(1) ▉\n```", "```py\nprint(1)\n```"),
        ("A block ▉ in the middle stays", "A block ▉ in the middle stays"),
        ("Finished text", "Finished text"),
    ],
)
def test_cursor_is_removed_from_unfinished_markup(service: Service, frame: str, shown: str) -> None:
    assert importlib.import_module("amsg_plugin.adapter").without_cursor(frame) == shown


def test_cancel_reaches_hermes_and_waits_for_confirmation(service: Service) -> None:
    async def scenario() -> None:
        gateway = FakeGateway("Part")
        gateway.hang = asyncio.Event()
        assert await gateway.adapter.connect()
        run_id = message(service, "x")["run"]["id"]
        await until(lambda: gateway.seen)
        await asyncio.sleep(0.2)
        response = httpx.post(f"{BASE}/v1/runs/{run_id}/cancel", headers=service.device)
        assert response.json()["state"] in ("cancel_requested", "cancelled")
        await until(lambda: any(e.text == "/stop" for e in gateway.seen))
        await until(lambda: any(m["status"] == "cancelled" and m["role"] == "agent" for m in transcript(service)))
        await gateway.adapter.disconnect()

    run(scenario())


def test_agent_tool_creates_a_space(service: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        gateway = FakeGateway()
        tools = importlib.import_module("amsg_plugin.tools")
        results: dict = {}
        monkeypatch.setenv("HERMES_SESSION_CHAT_ID", service.general)

        async def during(event) -> None:  # type: ignore[no-untyped-def]
            handler = tools._handler("create_space")
            results["first"] = json.loads(
                await asyncio.to_thread(handler, {"title": "Trip planning", "icon": "umbrella"})
            )
            results["again"] = json.loads(
                await asyncio.to_thread(handler, {"title": "Trip planning", "icon": "umbrella"})
            )
            results["bad"] = json.loads(await asyncio.to_thread(tools._handler("create_space"), {"title": ""}))
            results["unknown"] = json.loads(
                await asyncio.to_thread(tools._handler("rename_space"), {"space_id": "nope", "title": "x"})
            )

        gateway.during = during
        assert await gateway.adapter.connect()
        message(service, "make a space")
        await until(lambda: "unknown" in results)
        assert results["first"]["title"] == "Trip planning"
        assert results["again"]["space_id"] == results["first"]["space_id"]
        assert results["again"]["duplicate"] is True
        assert "error" in results["bad"]
        assert "error" in results["unknown"]
        spaces = httpx.get(f"{BASE}/v1/spaces", headers=service.device).json()
        assert [s["title"] for s in spaces].count("Trip planning") == 1
        await gateway.adapter.disconnect()

    run(scenario())


def test_success_with_nothing_sent_waits_for_the_replay(service: Service) -> None:
    """Hermes reports success for an event it queued at startup, then replays that same event."""

    async def scenario() -> None:
        gateway = FakeGateway("Answered after replay")
        adapter = gateway.adapter
        replayed: list = []

        async def queued_then_replayed(event) -> None:  # type: ignore[no-untyped-def]
            if not replayed:
                replayed.append(event)
                await adapter.on_processing_complete(event, gateway.event_mod.ProcessingOutcome.SUCCESS)
                await asyncio.sleep(0.3)
                assert [m["status"] for m in transcript(service) if m["role"] == "agent"] == ["running"]
                await adapter.on_processing_start(event)
                await gateway.process(event)

        adapter.on_event = queued_then_replayed
        assert await adapter.connect()
        message(service, "during startup")
        reply = await until(
            lambda: next((m for m in transcript(service) if m["role"] == "agent" and m["status"] == "completed"), None)
        )
        assert reply["markdown"] == "Answered after replay"
        await adapter.disconnect()

    run(scenario())


def test_no_reply_at_all_fails_rather_than_succeeding_empty(service: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        gateway = FakeGateway()
        adapter_module = importlib.import_module("amsg_plugin.adapter")
        monkeypatch.setattr(adapter_module, "EMPTY_REPLY_GRACE_SECONDS", 0.1)

        async def silent(event) -> None:  # type: ignore[no-untyped-def]
            await gateway.adapter.on_processing_complete(event, gateway.event_mod.ProcessingOutcome.SUCCESS)

        gateway.adapter.on_event = silent
        assert await gateway.adapter.connect()
        message(service, "x")
        reply = await until(
            lambda: next((m for m in transcript(service) if m["role"] == "agent" and m["status"] == "failed"), None)
        )
        assert reply["error_code"] == "empty_reply"
        await gateway.adapter.disconnect()

    run(scenario())


def test_tool_outside_a_turn_cannot_mutate(service: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    importlib.import_module("amsg_plugin.adapter")
    tools = importlib.import_module("amsg_plugin.tools")
    monkeypatch.setenv("HERMES_SESSION_CHAT_ID", service.general)
    assert "error" in json.loads(tools._handler("create_space")({"title": "Sneaky"}))


def test_connector_cannot_reach_another_connections_run(service: Service) -> None:
    from amsg import schemas

    domain = service.domain
    other = domain.create_connection(schemas.ConnectionCreate(name="Other", kind="bridge"))
    token = domain.issue_connector_token(other.id)
    run_id = message(service, "x")["run"]["id"]
    headers = {"Authorization": f"Bearer {token}"}
    for path, body in (
        (f"/v1/connector/runs/{run_id}/complete", {"markdown": "hijack"}),
        ("/v1/connector/tools/list_spaces", {"run_id": run_id, "arguments": {}}),
    ):
        assert httpx.post(f"{BASE}{path}", headers=headers, json=body).status_code == 403


def test_attachments_travel_both_ways(service: Service, tmp_path: Path) -> None:
    async def scenario() -> None:
        gateway = FakeGateway("File attached")
        report = tmp_path / "report.txt"
        report.write_text("generated")

        async def during(event) -> None:  # type: ignore[no-untyped-def]
            await gateway.adapter.send_document(event.source.chat_id, str(report))

        gateway.during = during
        assert await gateway.adapter.connect()
        uploaded = httpx.post(
            f"{BASE}/v1/uploads", headers=service.device, files={"file": ("in.txt", b"input", "text/plain")}
        ).json()
        httpx.post(
            f"{BASE}/v1/spaces/{service.general}/messages",
            headers=service.device,
            json={"client_message_id": str(uuid.uuid4()), "markdown": "read", "artifact_ids": [uploaded["id"]]},
        )
        reply = await until(
            lambda: next((m for m in transcript(service) if m["role"] == "agent" and m["status"] == "completed"), None)
        )
        event = gateway.seen[0]
        assert Path(event.media_urls[0]).read_bytes() == b"input"
        assert [a["display_name"] for a in reply["artifacts"]] == ["report.txt"]
        library = httpx.get(f"{BASE}/v1/artifacts", headers=service.device).json()["items"]
        assert sum(item["display_name"] == "report.txt" for item in library) == 1
        await gateway.adapter.disconnect()

    run(scenario())


def test_automation_commands_and_delivery(service: Service) -> None:
    async def scenario() -> None:
        gateway = FakeGateway()
        jobs = importlib.import_module("tools.cronjob_tools").JOBS
        jobs.clear()
        jobs["job-1"] = {
            "job_id": "job-1",
            "name": "Digest",
            "schedule": "every day",
            "deliver": f"amsg:{service.general}",
            "next_run_at": "2026-11-07T00:00:00Z",
            "state": "scheduled",
            "enabled": True,
        }
        assert await gateway.adapter.connect()
        automations = await until(lambda: httpx.get(f"{BASE}/v1/automations", headers=service.device).json())
        link = automations[0]
        assert link["space_id"] == service.general
        response = await asyncio.to_thread(
            httpx.post,
            f"{BASE}/v1/automations/{link['id']}/actions",
            headers=service.device,
            json={"action": "pause"},
            timeout=30,
        )
        assert response.status_code == 200, response.text
        assert jobs["job-1"]["state"] == "paused"
        sent = await gateway.adapter.send(service.general, "Scheduled result", metadata={"job_id": "job-1"})
        assert sent.success
        delivered = [m for m in transcript(service) if m["markdown"] == "Scheduled result"]
        assert delivered and delivered[0]["meta"]["automation_job_id"] == "job-1"
        await gateway.adapter.disconnect()

    run(scenario())


def test_tool_use_shows_on_the_running_reply_and_stays_out_of_it(
    service: Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_SESSION_CHAT_ID", service.general)

    def latest() -> dict:
        path = f"{BASE}/v1/spaces/{service.general}/messages"
        return httpx.get(path, headers=service.device).json()["items"][-1]

    async def scenario() -> None:
        gateway = FakeGateway()
        activity = importlib.import_module("amsg_plugin.activity")
        seen: list = []

        async def during(event) -> None:  # type: ignore[no-untyped-def]
            await asyncio.to_thread(activity.on_tool_start, tool_name="terminal", args={"command": "ls -la"})
            seen.append((await asyncio.to_thread(latest))["activity"])
            await asyncio.to_thread(activity.on_tool_end, tool_name="terminal", result="ok")
            seen.append((await asyncio.to_thread(latest))["activity"])

        gateway.during = during
        assert await gateway.adapter.connect()
        message(service, "list the files")
        await until(lambda: latest().get("status") == "completed")
        assert seen == ["Running ls -la", None]
        assert latest()["markdown"] == "**Done.**"
        await gateway.adapter.disconnect()

    run(scenario())


def test_tool_outside_a_turn_reports_nothing(service: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_SESSION_CHAT_ID", "another-platform-chat")
    activity = importlib.import_module("amsg_plugin.activity")
    monkeypatch.setattr(activity.httpx, "post", lambda *_, **__: pytest.fail("posted outside a turn"))
    activity.on_tool_start(tool_name="terminal", args={"command": "ls"})


def test_activity_describes_each_kind_of_tool(service: Service) -> None:
    activity = importlib.import_module("amsg_plugin.activity")
    assert activity.describe("web_search", {"query": "cats"}) == "Searching the web for cats"
    assert activity.describe("list_spaces", {}) == "List spaces"
    assert len(activity.describe("terminal", {"command": "x" * 900})) <= activity.LIMIT


def test_plugin_registers_its_tool_hooks(service: Service) -> None:
    plugin = importlib.import_module("amsg_plugin")
    hooks: dict = {}

    class Context:
        def register_hook(self, name, callback) -> None:  # type: ignore[no-untyped-def]
            hooks[name] = callback

        def __getattr__(self, _name):  # type: ignore[no-untyped-def]
            return lambda *_, **__: None

    plugin.register(Context())
    assert set(hooks) == {"pre_tool_call", "post_tool_call"}
