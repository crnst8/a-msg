"""Hermes platform adapter for a-msg.

Verified against hermes-agent v0.23.0 (2bd378ed). See docs/compatibility.md.
A space ID is the Hermes chat ID, so a space keeps one Hermes session. Work arrives from a durable
a-msg queue; results go back through routes scoped to this connector's credential.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    SendResult,
    cache_document_from_bytes,
    cache_image_from_bytes,
)
from gateway.platforms.event import MessageEvent, MessageType, ProcessingOutcome

from .client import ACTIVE_RUNS, OWNER_ID, error_text, headers, settings

log = logging.getLogger(__name__)
POLL_SECONDS = 20
SYNC_SECONDS = 120
EMPTY_REPLY_GRACE_SECONDS = 60.0
# Hermes adds this to each streamed frame, then closes any open markup after it. The reply card shows its own
# working state, and a run stopped mid-stream would otherwise keep the cursor in the saved message.
STREAM_CURSOR = re.compile(r" ▉(?=[`*_~\s]*$)")
EMPTY_SPAN_AT_CURSOR = re.compile(r"([`*_~]+) ▉\1\s*$")
HINT = (
    "You are chatting in a-msg, a desktop app. Each conversation is a space. Reply in CommonMark with "
    "headings 1-3, bold, italics, lists, quotes, fenced code and pipe tables. Do not use raw HTML. "
    "Change the app only through the a-msg tools; describing a change does not make it happen."
)


def without_cursor(text: str) -> str:
    return STREAM_CURSOR.sub("", EMPTY_SPAN_AT_CURSOR.sub("", text))


@dataclass
class Turn:
    """One a-msg run. Hermes may send several segments; they form one reply card."""

    run_id: str
    segments: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    done: bool = False
    empty_timer: asyncio.Task[None] | None = None
    stopping: bool = False

    def body(self) -> str:
        return "\n\n".join(part for part in self.segments if part)


def _context_prompt(context: dict[str, Any]) -> str:
    """The turn envelope. Applied at call time and not persisted into the transcript by Hermes."""
    lines = [
        f"a-msg space: {context['space']['title']} (id {context['space']['id']}, kind {context['space']['kind']}).",
        f"Available a-msg tools: {', '.join(context['tools'])}.",
    ]
    if context.get("artifacts"):
        files = ", ".join(f"{a['name']} ({a['mime']}, id {a['id']})" for a in context["artifacts"])
        lines.append(f"Attached files: {files}.")
    source = context.get("source_context")
    if source:
        lines.append("This space continues selected messages from another space.")
        lines.append(f"Selected source message IDs: {', '.join(source['selected_source_ids'])}.")
        lines.append(f"Selected text:\n{source['excerpt']}")
        if source.get("generated_summary"):
            lines.append(f"Generated summary, not a source of record: {source['generated_summary']}")
        if source.get("truncated"):
            lines.append("That text is a bounded excerpt. Use read_thread_context for the full selection.")
    return "\n".join(lines)


def _host_timezone() -> str:
    """The IANA name of this machine's zone, for a profile that does not set one."""
    if os.environ.get("TZ"):
        return os.environ["TZ"].lstrip(":")
    try:
        target = os.path.realpath("/etc/localtime")
        return target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else "UTC"
    except OSError:
        return "UTC"


def _profile_timezone() -> str:
    """A job with no timezone of its own runs on the profile's clock, which may not be UTC."""
    try:
        from hermes_time import get_timezone_name

        return get_timezone_name() or _host_timezone()
    except Exception:
        return _host_timezone()


def _report(job: dict[str, Any]) -> dict[str, Any]:
    """Translates a Hermes job record into the a-msg automation report."""
    deliver = job.get("deliver") or ""
    targets = deliver if isinstance(deliver, list) else [deliver]
    destination = next((t for t in targets if isinstance(t, str) and t.startswith("amsg:")), None)
    return {
        "provider_job_id": job["job_id"],
        "name": job.get("name") or job["job_id"],
        "schedule": str(job.get("schedule") or ""),
        "timezone": job.get("timezone") or _profile_timezone(),
        "next_run_at": job.get("next_run_at"),
        "last_run_at": job.get("last_run_at"),
        "last_outcome": job.get("last_status"),
        "state": "paused" if job.get("state") == "paused" or job.get("enabled") is False else "active",
        "destination": destination,
    }


def _cron(action: str, **arguments: Any) -> dict[str, Any]:
    """Hermes' own management entry point; job storage is never edited from here."""
    from tools.cronjob_tools import cronjob

    result = json.loads(cronjob(action=action, **arguments))
    if result.get("success") is False:
        raise RuntimeError(str(result.get("error") or "The scheduler rejected this change."))
    return result


class AmsgAdapter(BasePlatformAdapter):
    def __init__(self, config: PlatformConfig) -> None:
        super().__init__(config, Platform("amsg"))
        self.url, self.token = settings()
        self._http: httpx.AsyncClient | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        self._turns: dict[str, Turn] = {}
        self._events: dict[str, MessageEvent] = {}

    @property
    def http(self) -> httpx.AsyncClient:
        assert self._http is not None
        return self._http

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        self.url, self.token = settings()
        self._http = httpx.AsyncClient(base_url=self.url, headers=headers(self.token), timeout=40.0)
        try:
            response = await self.http.post("/v1/connector/capabilities", json=self.capabilities())
            response.raise_for_status()
        except httpx.HTTPError as error:
            await self.http.aclose()
            self._http = None
            log.warning("a-msg is not reachable: %s", error)
            return False
        self._tasks = [asyncio.create_task(self._poll()), asyncio.create_task(self._sync_loop())]
        self._mark_connected()
        return True

    async def disconnect(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        self._mark_disconnected()

    def capabilities(self) -> dict[str, Any]:
        return {
            "streaming": True,
            "streaming_mode": "edit",
            "cancel": True,
            "artifacts": True,
            "automations": True,
            "sessions": True,
        }

    # Inbound -------------------------------------------------------------------------------------------
    async def _poll(self) -> None:
        delay = 1.0
        while True:
            try:
                response = await self.http.get("/v1/connector/dispatch", params={"wait": POLL_SECONDS})
                response.raise_for_status()
                delay = 1.0
                job = response.json()
                if job:
                    await self._dispatch(job)
            except asyncio.CancelledError:
                raise
            except httpx.HTTPError as error:
                log.info("a-msg poll failed: %s", error)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
            except Exception:
                log.exception("a-msg dispatch failed")

    async def _dispatch(self, job: dict[str, Any]) -> None:
        if job["kind"] == "message":
            await self._on_message(job)
        elif job["kind"] == "cancel":
            await self._on_cancel(job)
        elif job["kind"] == "command":
            asyncio.create_task(self._on_command(job))

    def _source(self, space_id: str, title: str) -> Any:
        return self.build_source(chat_id=space_id, chat_name=title, chat_type="dm", user_id=OWNER_ID, user_name="a-msg")

    async def _download(self, run_id: str, artifact: dict[str, Any]) -> tuple[str, str] | None:
        response = await self.http.get(f"/v1/connector/artifacts/{artifact['id']}/content", params={"run_id": run_id})
        if response.status_code != 200:
            return None
        if artifact["mime"].startswith("image/"):
            extension = Path(artifact["name"]).suffix or ".png"
            return cache_image_from_bytes(response.content, extension), artifact["mime"]
        return cache_document_from_bytes(response.content, artifact["name"]), artifact["mime"]

    async def _on_message(self, job: dict[str, Any]) -> None:
        context = job["payload"]["context"]
        space_id = job["space_id"]
        run_id = job["run_id"]
        self._turns[space_id] = Turn(run_id=run_id)
        ACTIVE_RUNS[space_id] = run_id
        media = [await self._download(run_id, artifact) for artifact in context.get("artifacts", [])]
        media = [item for item in media if item]
        kind = MessageType.TEXT
        if media:
            kind = MessageType.PHOTO if media[0][1].startswith("image/") else MessageType.DOCUMENT
        event = MessageEvent(
            text=context["text"],
            message_type=kind,
            user_id=OWNER_ID,
            user_name="a-msg",
            source=self._source(space_id, context["space"]["title"]),
            message_id=context["request_message_id"],
            media_urls=[path for path, _ in media],
            media_types=[mime for _, mime in media],
            channel_prompt=_context_prompt(context),
        )
        self._events[space_id] = event
        await self.http.post(f"/v1/connector/dispatch/{job['id']}/ack", json={"provider_run_id": run_id})
        await self.handle_message(event)

    async def _on_cancel(self, job: dict[str, Any]) -> None:
        running = next(((space, turn) for space, turn in self._turns.items() if turn.run_id == job["run_id"]), None)
        if running is None:
            await self.http.post(f"/v1/connector/runs/{job['run_id']}/cancelled")
            return
        space_id, turn = running
        turn.stopping = True
        original = self._events.get(space_id)
        title = original.source.chat_name if original else ""
        await self.handle_message(
            MessageEvent(
                text="/stop",
                message_type=MessageType.COMMAND,
                user_id=OWNER_ID,
                user_name="a-msg",
                source=self._source(space_id, title),
            )
        )

    async def _on_command(self, job: dict[str, Any]) -> None:
        command = job["payload"]["command"]
        arguments = job["payload"].get("arguments") or {}
        try:
            result = await asyncio.to_thread(self._run_command, command, arguments)
            body = {"ok": True, "result": result}
        except Exception as error:
            body = {"ok": False, "error": str(error)[:500]}
        await self.http.post(f"/v1/connector/dispatch/{job['id']}/result", json=body)

    def _run_command(self, command: str, arguments: dict[str, Any]) -> dict[str, Any]:
        job_id = arguments.get("job_id")
        if command == "probe":
            return {"platform": "amsg", "connected": self.is_connected, "formats": self.capabilities()}
        if command == "list":
            return {"jobs": [_report(job) for job in _cron("list", include_disabled=True).get("jobs", [])]}
        if command in ("pause", "resume", "remove"):
            _cron(command, job_id=job_id)
        elif command == "run_now":
            _cron("run", job_id=job_id)
        elif command == "redirect":
            _cron("update", job_id=job_id, deliver=arguments["destination"])
        else:
            raise ValueError(f"Unknown command: {command}")
        return {}

    async def _sync_loop(self) -> None:
        while True:
            try:
                jobs = await asyncio.to_thread(self._run_command, "list", {})
                await self.http.post("/v1/connector/automations/sync", json=jobs)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log.info("a-msg automation sync failed: %s", error)
            await asyncio.sleep(SYNC_SECONDS)

    # Outbound ------------------------------------------------------------------------------------------
    async def _push(self, turn: Turn) -> SendResult:
        response = await self.http.put(f"/v1/connector/runs/{turn.run_id}/body", json={"markdown": turn.body()})
        if response.status_code >= 300:
            return SendResult(success=False, error=error_text(response))
        return SendResult(success=True, message_id=f"{turn.run_id}:{len(turn.segments) - 1}")

    async def send(
        self, chat_id: str, content: str, reply_to: str | None = None, metadata: dict[str, Any] | None = None
    ) -> SendResult:
        turn = self._turns.get(chat_id)
        if turn is None or turn.done:
            return await self._post(chat_id, content, (metadata or {}))
        if turn.stopping:
            # Hermes' own "stopped" notice. a-msg shows run state on the card.
            return SendResult(success=True, message_id=f"{turn.run_id}:notice")
        turn.segments.append(without_cursor(content))
        return await self._push(turn)

    async def _post(self, chat_id: str, content: str, metadata: dict[str, Any]) -> SendResult:
        response = await self.http.post(
            "/v1/connector/messages",
            json={"space_id": chat_id, "markdown": content, "automation_job_id": metadata.get("job_id")},
        )
        if response.status_code >= 300:
            return SendResult(success=False, error=error_text(response))
        return SendResult(success=True, message_id=response.json()["id"])

    async def edit_message(self, chat_id: str, message_id: str, content: str, *, finalize: bool = False) -> SendResult:
        turn = self._turns.get(chat_id)
        if turn is None or turn.done or ":" not in message_id:
            return SendResult(success=False, error="That message can no longer be edited.")
        run_id, _, index = message_id.rpartition(":")
        if run_id != turn.run_id or not index.isdigit() or int(index) >= len(turn.segments):
            return SendResult(success=False, error="That message can no longer be edited.")
        turn.segments[int(index)] = without_cursor(content)
        result = await self._push(turn)
        return SendResult(success=result.success, message_id=message_id, error=result.error)

    async def _upload(self, chat_id: str, path: str, name: str | None) -> SendResult:
        turn = self._turns.get(chat_id)
        file = Path(path)
        params = {"run_id": turn.run_id} if turn and not turn.done else {"space_id": chat_id}
        with file.open("rb") as handle:
            response = await self.http.post(
                "/v1/connector/uploads",
                params=params,
                files={"file": (name or file.name, handle)},
                timeout=None,
            )
        if response.status_code >= 300:
            return SendResult(success=False, error=error_text(response))
        artifact = response.json()
        if turn and not turn.done:
            turn.artifacts.append(artifact["id"])
        else:
            await self.http.post(
                "/v1/connector/messages",
                json={"space_id": chat_id, "markdown": "", "artifact_ids": [artifact["id"]]},
            )
        return SendResult(success=True, message_id=artifact["id"])

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: str | None = None,
        file_name: str | None = None,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> SendResult:
        if caption:
            await self.send(chat_id, caption, reply_to, metadata)
        return await self._upload(chat_id, file_path, file_name)

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: str | None = None,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> SendResult:
        return await self.send_document(chat_id, image_path, caption, None, reply_to, metadata)

    async def send_typing(self, chat_id: str, metadata: dict[str, Any] | None = None) -> None:
        """Working status comes from run state; a typing ping adds nothing truthful."""

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        return {"name": chat_id, "type": "dm"}

    async def on_processing_start(self, event: MessageEvent) -> None:
        chat_id = event.source.chat_id
        turn = self._turns.get(chat_id)
        if turn is not None:
            if turn.empty_timer is not None:
                turn.empty_timer.cancel()
                turn.empty_timer = None
            await self.http.post(f"/v1/connector/runs/{turn.run_id}/activity", json={"text": None})

    async def on_processing_complete(self, event: MessageEvent, outcome: ProcessingOutcome) -> None:
        chat_id = event.source.chat_id
        turn = self._turns.get(chat_id)
        if turn is None or event.message_type == MessageType.COMMAND:
            return
        if outcome == ProcessingOutcome.SUCCESS and not turn.body() and not turn.artifacts:
            # Success with nothing sent means Hermes queued the event, for example during startup
            # restore. It replays the same event through handle_message, so the run stays open.
            if turn.empty_timer is None:
                turn.empty_timer = asyncio.create_task(self._expire_empty(chat_id, turn))
            return
        if turn.empty_timer is not None:
            turn.empty_timer.cancel()
            turn.empty_timer = None
        turn.done = True
        self._turns.pop(chat_id, None)
        self._events.pop(chat_id, None)
        ACTIVE_RUNS.pop(chat_id, None)
        if outcome == ProcessingOutcome.SUCCESS:
            await self.http.post(
                f"/v1/connector/runs/{turn.run_id}/complete",
                json={"markdown": turn.body(), "artifact_ids": turn.artifacts},
            )
        elif outcome == ProcessingOutcome.CANCELLED:
            await self.http.post(f"/v1/connector/runs/{turn.run_id}/cancelled")
        else:
            await self.http.post(
                f"/v1/connector/runs/{turn.run_id}/fail",
                json={"code": "provider_error", "message": "Hermes reported a failure."},
            )

    async def _expire_empty(self, chat_id: str, turn: Turn) -> None:
        """No replay arrived: report that no reply exists rather than an empty success."""
        await asyncio.sleep(EMPTY_REPLY_GRACE_SECONDS)
        turn.empty_timer = None
        turn.done = True
        self._turns.pop(chat_id, None)
        self._events.pop(chat_id, None)
        ACTIVE_RUNS.pop(chat_id, None)
        await self.http.post(
            f"/v1/connector/runs/{turn.run_id}/fail",
            json={"code": "empty_reply", "message": "The agent returned no reply."},
        )


def check_requirements() -> bool:
    url, token = settings()
    return bool(url and token)


def validate_config(config: Any) -> bool:
    return check_requirements()


def is_connected(config: Any) -> bool:
    return check_requirements()


def _env_enablement() -> dict[str, Any] | None:
    return {} if check_requirements() else None


async def _standalone_send(
    pconfig: Any,
    chat_id: str,
    message: str,
    *,
    thread_id: str | None = None,
    media_files: list[str] | None = None,
    force_document: bool = False,
) -> dict[str, Any]:
    """Out-of-process delivery for scheduled jobs when the gateway is not running in this process."""
    url, token = settings()
    if not url or not token:
        return {"success": False, "error": "a-msg is not configured"}
    async with httpx.AsyncClient(base_url=url, headers=headers(token), timeout=30.0) as client:
        response = await client.post("/v1/connector/messages", json={"space_id": chat_id, "markdown": message})
    if response.status_code >= 300:
        return {"success": False, "error": error_text(response)}
    return {"success": True, "platform": "amsg", "chat_id": chat_id, "message_id": response.json()["id"]}


def register(ctx: Any) -> None:
    from gateway.platform_registry import PlatformEntry
    from gateway.platforms.send_targets import parse_nonempty

    arguments: dict[str, Any] = dict(
        name="amsg",
        label="a-msg",
        adapter_factory=AmsgAdapter,
        check_fn=check_requirements,
        validate_config=validate_config,
        required_env=["AMSG_URL", "AMSG_CONNECTOR_TOKEN"],
        install_hint="Run `amsg setup --hermes-env <profile>/.env`",
        is_connected=is_connected,
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var="AMSG_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        allowed_users_env="AMSG_ALLOWED_USERS",
        max_message_length=0,
        emoji="🟫",
        pii_safe=True,
        platform_hint=HINT,
    )
    known = {item.name for item in dataclasses.fields(PlatformEntry)}
    for key, value in (
        ("target_ref_syntax_fn", parse_nonempty),
        ("parse_target_ref_fn", parse_nonempty),
        ("display_tier", "low"),
        ("display_overrides", {"streaming": None}),
    ):
        if key in known:
            arguments[key] = value
    ctx.register_platform(**arguments)
