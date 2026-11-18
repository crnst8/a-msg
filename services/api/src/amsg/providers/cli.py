"""Coding agents driven through their structured command-line protocols.

Text from chat only ever travels as one element of an argument array, never through a shell. Each run has a
process group, so Stop can reach child processes and the run reports what actually happened.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import access as levels
from .base import Provider, ProviderError, RunSink

if TYPE_CHECKING:
    from ..db.models import AgentConnection
    from ..domain import Domain

log = logging.getLogger("amsg.cli")
OUTBOX = ".amsg/outbox"
INBOX = ".amsg/inbox"
LINE_LIMIT = 4 * 1024 * 1024
OUTPUT_LIMIT = 2 * 1024 * 1024
TERMINATE_GRACE = 5.0
PREAMBLE = (
    f"To return a file to the user, write it to {OUTBOX}/ inside the working directory. "
    "Those files appear as attachments. Reply in CommonMark.\n\n"
)
ENV_KEYS = {"codex": "OPENAI_API_KEY", "claude": "ANTHROPIC_API_KEY"}


@dataclass
class Parsed:
    text: str | None = None
    activity: str | None = None
    session_id: str | None = None
    final: bool = False
    error: str | None = None


def describe_tool(name: str, value: dict[str, Any]) -> str:
    detail = value.get("command") or value.get("file_path") or value.get("path") or ""
    return f"{name} {detail}".strip()[:200]


def parse_codex(event: dict[str, Any]) -> Parsed:
    kind = event.get("type")
    if kind == "thread.started":
        return Parsed(session_id=event.get("thread_id"))
    item = event.get("item") or {}
    if kind == "item.started" and item.get("type") == "command_execution":
        return Parsed(activity=f"Running {str(item.get('command', ''))[:160]}")
    if kind == "item.completed" and item.get("type") == "agent_message":
        return Parsed(text=item.get("text", ""))
    if kind == "turn.completed":
        return Parsed(final=True)
    if kind in ("turn.failed", "error"):
        error = event.get("error") or {}
        return Parsed(error=(error.get("message") if isinstance(error, dict) else str(error)) or event.get("message"))
    return Parsed()


def parse_claude(event: dict[str, Any]) -> Parsed:
    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "init":
        return Parsed(session_id=event.get("session_id"))
    if kind == "assistant":
        content = (event.get("message") or {}).get("content") or []
        text = "".join(block.get("text", "") for block in content if block.get("type") == "text")
        tool = next((block for block in content if block.get("type") == "tool_use"), None)
        return Parsed(
            text=text or None,
            activity=describe_tool(tool.get("name", "Tool"), tool.get("input") or {}) if tool else None,
        )
    if kind == "result":
        if event.get("is_error"):
            return Parsed(error=str(event.get("result") or "The agent reported a failure."))
        return Parsed(final=True)
    return Parsed()


def build_argv(
    connection: AgentConnection, prompt: str, directory: str, session: str | None, access: str | None = None
) -> list[str]:
    config = connection.config or {}
    program = config.get("command") or connection.kind
    if connection.kind == "codex":
        sandbox = levels.CODEX_SANDBOX[access] if access else config.get("sandbox", "workspace-write")
        arguments = [program, "exec"]
        if session:
            # `exec resume` has no --sandbox flag, and the sandbox must not depend on what config.toml says.
            arguments += ["resume", session, "-c", f"sandbox_mode={json.dumps(sandbox)}"]
        else:
            arguments += ["--sandbox", sandbox, "--cd", directory]
        return [*arguments, "--json", "--skip-git-repo-check", "--", prompt]
    permission = levels.CLAUDE_MODE[access] if access else config.get("permission_mode", "acceptEdits")
    arguments = [program, "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", permission]
    if session:
        arguments += ["--resume", session]
    return [*arguments, "--", prompt]


class CliCodingProvider(Provider):
    def __init__(self, domain: Domain) -> None:
        self.domain = domain
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._stopping: set[str] = set()

    def features(self, connection: AgentConnection) -> dict[str, Any]:
        config = connection.config or {}
        return {
            "default_access": levels.default_level(connection.kind, config),
            "max_access": levels.ceiling(connection.kind, config),
            "streaming": True,
            "streaming_mode": "message",
            "cancel": True,
            "artifacts": True,
            "automations": False,
            "sessions": True,
        }

    async def test(self, connection: AgentConnection) -> list[dict[str, Any]]:
        program = (connection.config or {}).get("command") or connection.kind
        path = shutil.which(program)
        if path is None:
            return [{"name": "program", "ok": False, "detail": f"{program} was not found on this host."}]
        process = await asyncio.create_subprocess_exec(
            path, "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=20)
        except TimeoutError:
            process.kill()
            return [{"name": "program", "ok": False, "detail": f"{program} did not respond."}]
        roots = (connection.config or {}).get("allowed_roots") or []
        top = levels.ceiling(connection.kind, connection.config or {})
        checks = [
            {"name": "program", "ok": process.returncode == 0, "detail": output.decode(errors="replace").strip()[:200]},
            {
                "name": "folders",
                "ok": any(Path(root).expanduser().is_dir() for root in roots),
                "detail": f"{len(roots)} allowed",
            },
            {"name": "access", "ok": True, "detail": f"Sessions may use up to {levels.NAMES[top]}"},
        ]
        if connection.kind == "codex":
            checks.append(await self._codex_login(path))
        return checks

    async def _codex_login(self, path: str) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            path, "login", "status", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=20)
        except TimeoutError:
            process.kill()
            return {"name": "login", "ok": False, "detail": "codex login status did not respond."}
        if process.returncode != 0:
            return {"name": "login", "ok": False, "detail": "Not logged in. Run codex login on this host."}
        method = "ChatGPT" if b"ChatGPT" in output else "an API key"
        return {"name": "login", "ok": True, "detail": f"Logged in with {method}"}

    async def run(self, connection: AgentConnection, job: dict[str, Any], sink: RunSink) -> None:
        context = job["context"]
        directory = context.get("working_directory")
        if not directory:
            raise ProviderError("directory_required", "This session has no working directory.")
        self.domain._validate_directory(connection, directory)
        access = context.get("access")
        if access is not None and not levels.allows(levels.ceiling(connection.kind, connection.config or {}), access):
            # Checked here as well as in the service: on a runner, only this host's flags set the ceiling.
            raise ProviderError("access_not_allowed", f"This host does not allow {levels.NAMES.get(access, access)}.")
        text = context["text"] + await self._receive(Path(directory), context, sink)
        prompt = PREAMBLE + text if context.get("provider_session_id") is None else text
        argv = build_argv(connection, prompt, directory, context.get("provider_session_id"), access)
        env = dict(os.environ)
        secrets = await asyncio.to_thread(self.domain.connection_secrets, connection.id)
        if secrets.get("api_key") and connection.kind in ENV_KEYS:
            env[ENV_KEYS[connection.kind]] = secrets["api_key"]
        parser = parse_codex if connection.kind == "codex" else parse_claude
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=directory,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=LINE_LIMIT,
            )
        except OSError as error:
            raise ProviderError("provider_unavailable", f"{connection.name} could not start: {error}") from error
        self._processes[sink.run_id] = process
        started = asyncio.get_running_loop().time()
        written = 0
        final = False
        failure: str | None = None
        stderr_task = asyncio.create_task(process.stderr.read(8192))  # type: ignore[union-attr]
        try:
            assert process.stdout is not None
            async for raw in process.stdout:
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                parsed = parser(event)
                if parsed.session_id:
                    await sink.session(parsed.session_id)
                if parsed.activity:
                    await sink.activity(parsed.activity)
                if parsed.text and written < OUTPUT_LIMIT:
                    chunk = parsed.text if written == 0 else "\n\n" + parsed.text
                    await sink.delta(chunk[: OUTPUT_LIMIT - written])
                    written += len(chunk)
                final = final or parsed.final
                failure = failure or parsed.error
            code = await process.wait()
        finally:
            self._processes.pop(sink.run_id, None)
        detail = (await stderr_task).decode(errors="replace").strip()
        stopped = sink.run_id in self._stopping
        self._stopping.discard(sink.run_id)
        if stopped:
            await sink.cancelled()
            return
        if failure or code != 0 or not final:
            raise ProviderError("provider_error", failure or detail[-500:] or f"The agent exited with code {code}.")
        await self._collect(Path(directory), sink, started)
        await sink.complete()

    async def _receive(self, directory: Path, context: dict[str, Any], sink: RunSink) -> str:
        """Puts the message's attachments in the working directory and names them in the prompt."""
        artifacts = context.get("artifacts") or []
        if not artifacts:
            return ""
        inbox = directory / INBOX / context["request_message_id"]
        await asyncio.to_thread(inbox.mkdir, parents=True, exist_ok=True)
        names = []
        for artifact in artifacts:
            name = Path(artifact["name"]).name or artifact["id"]
            data = await sink.attachment(artifact["id"])
            await asyncio.to_thread((inbox / name).write_bytes, data)
            names.append(f"- {INBOX}/{context['request_message_id']}/{name}")
        return "\n\nAttached files:\n" + "\n".join(names)

    async def _collect(self, directory: Path, sink: RunSink, started: float) -> None:
        outbox = directory / OUTBOX
        if not outbox.is_dir():
            return
        for path in sorted(outbox.iterdir()):
            if path.is_symlink() or not path.is_file():
                continue
            await sink.artifact_path_within(path, directory)
            path.unlink(missing_ok=True)

    async def cancel(self, connection: AgentConnection, run_id: str) -> bool:
        process = self._processes.get(run_id)
        if process is None:
            return False
        self._stopping.add(run_id)
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return True

        async def force() -> None:
            await asyncio.sleep(TERMINATE_GRACE)
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        asyncio.create_task(force())
        return True
