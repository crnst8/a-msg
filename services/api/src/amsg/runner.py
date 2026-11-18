"""Runs a coding agent on this host for an a-msg service on another host.

The runner connects outward with a connector credential and claims work through the same routes as the Hermes plugin.
The agent uses its own login here, so no API key leaves this machine. Program, sandbox, the highest access a session
may choose and allowed folders come from this host's flags, never from the service: the service supplies only the
text, a folder and an access level to check against them.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import signal
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

from . import access as levels
from .providers.base import ProviderError
from .providers.cli import TERMINATE_GRACE, CliCodingProvider

log = logging.getLogger("amsg.runner")
POLL_SECONDS = 20.0
HEARTBEAT_SECONDS = 60.0
DELTA_PATIENCE = 30.0
FINISH_PATIENCE = 600.0  # Inside the service's 15-minute lease.
MAX_BACKOFF = 15.0


class Link:
    """Requests to the service. Transport failures and 5xx responses are retried until the patience runs out."""

    def __init__(self, url: str, token: str) -> None:
        self.http = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(30.0, read=POLL_SECONDS + 15.0),
        )

    async def send(self, method: str, path: str, *, patience: float, **options: Any) -> httpx.Response:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + patience
        delay = 1.0
        while True:
            try:
                response = await self.http.request(method, f"/v1/connector{path}", **options)
                if response.status_code < 500:
                    return response
                failure: Exception = httpx.HTTPStatusError("server error", request=response.request, response=response)
            except httpx.TransportError as error:
                failure = error
            if loop.time() + delay > deadline:
                raise failure
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_BACKOFF)


class RemoteSink:
    """The provider's write side, carried over HTTP."""

    def __init__(self, runner: Runner, job: dict[str, Any]) -> None:
        self.runner = runner
        self.run_id: str = job["run_id"]
        self.space_id: str = job["space_id"]
        self.message_id: str = job["message_id"]
        self.current: str | None = None
        self.done = False
        self._text: list[str] = []
        self._lost = False
        self._artifacts: list[str] = []

    def _send(self, path: str, patience: float = FINISH_PATIENCE, **options: Any) -> Any:
        return self.runner.link.send("POST", f"/runs/{self.run_id}{path}", patience=patience, **options)

    async def session(self, provider_session_id: str) -> None:
        await self._send("/session", json={"provider_run_id": provider_session_id})

    async def delta(self, text: str) -> None:
        self._text.append(text)
        try:
            response = await self._send("/delta", patience=DELTA_PATIENCE, json={"append": text})
            self._lost = self._lost or response.status_code != 204
        except httpx.HTTPError:
            self._lost = True

    async def activity(self, text: str | None) -> None:
        self.current = text[:300] if text else None
        try:
            await self._send("/activity", patience=DELTA_PATIENCE, json={"text": self.current})
        except httpx.HTTPError:
            log.info("activity not delivered", extra={"run_id": self.run_id})

    async def attachment(self, artifact_id: str) -> bytes:
        response = await self.runner.link.send(
            "GET", f"/artifacts/{artifact_id}/content", patience=DELTA_PATIENCE, params={"run_id": self.run_id}
        )
        if response.status_code != 200:
            raise ProviderError("provider_error", "An attachment could not be fetched.")
        return response.content

    async def artifact_path_within(self, path: Path, root: Path) -> None:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root.resolve()):
            raise ProviderError("path_not_allowed", "The agent returned a file outside its working directory.")
        data = await asyncio.to_thread(resolved.read_bytes)
        response = await self.runner.link.send(
            "POST",
            "/uploads",
            patience=FINISH_PATIENCE,
            params={"run_id": self.run_id},
            files={"file": (path.name, data)},
        )
        if response.status_code != 201:
            raise ProviderError("provider_error", f"{path.name} could not be returned.")
        self._artifacts.append(response.json()["id"])

    async def complete(self, markdown: str | None = None) -> None:
        if markdown is None and self._lost:
            markdown = "".join(self._text)
        await self._send("/complete", json={"markdown": markdown, "artifact_ids": self._artifacts})
        self.done = True

    async def fail(self, code: str, message: str) -> None:
        await self._send("/fail", json={"code": code, "message": message[:2000]})
        self.done = True

    async def cancelled(self) -> None:
        if self.runner.stopping.is_set():
            await self.fail("interrupted", "The runner stopped during this run.")
            return
        await self._send("/cancelled")
        self.done = True


class Host:
    """What the provider needs from its surroundings, answered from this machine."""

    def __init__(self, roots: list[Path]) -> None:
        self.roots = roots

    def _validate_directory(self, _connection: Any, directory: str) -> str:
        resolved = Path(directory).resolve()
        if not any(resolved.is_relative_to(root) for root in self.roots):
            raise ProviderError("directory_not_allowed", "That working directory is outside the allowed roots.")
        if not resolved.is_dir():
            raise ProviderError("directory_missing", f"{directory} does not exist on {socket.gethostname()}.")
        return str(resolved)

    def connection_secrets(self, _connection_id: str) -> dict[str, str]:
        return {}


class Runner:
    def __init__(self, url: str, token: str, *, agent: str, roots: list[Path], config: dict[str, Any]) -> None:
        self.link = Link(url, token)
        self.roots = [root.expanduser().resolve() for root in roots]
        # Sessions may lower access but not raise it above this host's default unless max_access says so.
        top = config.get("max_access") or levels.default_level(agent, config)
        self.connection = SimpleNamespace(
            id="runner",
            name=agent,
            kind=agent,
            config={**config, "max_access": top, "allowed_roots": [str(root) for root in self.roots]},
        )
        self.provider = CliCodingProvider(Host(self.roots))  # type: ignore[arg-type]
        self.stopping = asyncio.Event()
        self.runs: dict[str, asyncio.Task[None]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def capabilities(self) -> dict[str, Any]:
        config = self.connection.config
        return {
            "agent": self.connection.kind,
            "default_access": levels.default_level(self.connection.kind, config),
            "max_access": levels.ceiling(self.connection.kind, config),
            "host": socket.gethostname(),
            "allowed_roots": self.connection.config["allowed_roots"],
            "streaming": True,
            "streaming_mode": "message",
            "cancel": True,
            "artifacts": True,
            "automations": False,
            "sessions": True,
        }

    def _spawn(self, coroutine: Any) -> asyncio.Task[None]:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def serve(self) -> None:
        poll = asyncio.create_task(self._poll())
        await self.stopping.wait()
        poll.cancel()
        await asyncio.gather(poll, return_exceptions=True)
        for run_id in list(self.runs):
            await self.provider.cancel(self.connection, run_id)  # type: ignore[arg-type]
        if self.runs:
            await asyncio.wait(list(self.runs.values()), timeout=TERMINATE_GRACE + 3)
        await self.link.http.aclose()

    async def _poll(self) -> None:
        delay = 1.0
        announced = False
        while True:
            try:
                if not announced:
                    response = await self.link.send("POST", "/capabilities", patience=0, json=self.capabilities())
                    response.raise_for_status()
                    announced = True
                    log.info("connected")
                response = await self.link.send("GET", "/dispatch", patience=0, params={"wait": POLL_SECONDS})
                response.raise_for_status()
                delay = 1.0
                job = response.json()
                if job:
                    self._accept(job)
            except asyncio.CancelledError:
                raise
            except httpx.HTTPError as error:
                log.info("poll failed: %s", error)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    def _accept(self, job: dict[str, Any]) -> None:
        if job["kind"] == "message":
            self.runs[job["run_id"]] = self._spawn(self._run(job))
        elif job["kind"] == "cancel":
            self._spawn(self._cancel(job["run_id"]))
        elif job["kind"] == "command":
            self._spawn(self._command(job))

    async def _run(self, job: dict[str, Any]) -> None:
        sink = RemoteSink(self, job)
        heartbeat = asyncio.create_task(self._heartbeat(sink))
        work = {
            "run_id": job["run_id"],
            "space_id": job["space_id"],
            "reply_message_id": job["message_id"],
            "context": job["payload"]["context"],
        }
        try:
            await self.link.send(
                "POST", f"/dispatch/{job['id']}/ack", patience=DELTA_PATIENCE, json={"provider_run_id": job["run_id"]}
            )
            await self.provider.run(self.connection, work, sink)  # type: ignore[arg-type]
            if not sink.done:
                await sink.complete()
        except ProviderError as error:
            await sink.fail(error.code, error.message)
        except httpx.HTTPError as error:
            # The service stayed unreachable past the patience; its lease reports this run as uncertain.
            log.warning("run result not delivered: %s", error, extra={"run_id": job["run_id"]})
        except Exception:
            log.exception("run failed", extra={"run_id": job["run_id"]})
            await sink.fail("provider_error", "The agent stopped unexpectedly.")
        finally:
            heartbeat.cancel()
            self.runs.pop(job["run_id"], None)

    async def _heartbeat(self, sink: RemoteSink) -> None:
        """Holds the lease through quiet stretches. A run the service has ended is stopped here too."""
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                response = await self.link.send(
                    "POST",
                    f"/runs/{sink.run_id}/activity",
                    patience=HEARTBEAT_SECONDS / 2,
                    json={"text": sink.current},
                )
            except httpx.HTTPError:
                continue
            if response.status_code == 403:
                await self.provider.cancel(self.connection, sink.run_id)  # type: ignore[arg-type]
                return

    async def _cancel(self, run_id: str) -> None:
        task = self.runs.get(run_id)
        if task is None:
            await self.link.send("POST", f"/runs/{run_id}/cancelled", patience=FINISH_PATIENCE)
            return
        # Stop can arrive while the run is still fetching attachments, before there is a process to signal.
        while not task.done():
            if await self.provider.cancel(self.connection, run_id):  # type: ignore[arg-type]
                return
            await asyncio.sleep(0.1)

    async def _command(self, job: dict[str, Any]) -> None:
        if job["payload"].get("command") == "probe":
            checks = await self.provider.test(self.connection)  # type: ignore[arg-type]
            body: dict[str, Any] = {
                "ok": True,
                "result": {"platform": "amsg-runner", "formats": self.capabilities(), "checks": checks},
            }
        else:
            body = {"ok": False, "error": "This connection has no scheduler."}
        await self.link.send("POST", f"/dispatch/{job['id']}/result", patience=DELTA_PATIENCE, json=body)


async def _serve(runner: Runner) -> None:
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, runner.stopping.set)
    await runner.serve()


def build_runner(args: argparse.Namespace, url: str, token: str) -> Runner:
    config: dict[str, Any] = {"command": args.program or args.agent}
    if args.sandbox:
        config["sandbox"] = args.sandbox
    if args.permission_mode:
        config["permission_mode"] = args.permission_mode
    if getattr(args, "max_access", None):
        config["max_access"] = args.max_access
    return Runner(url, token, agent=args.agent, roots=[Path(root) for root in args.root], config=config)


def main(args: argparse.Namespace) -> int:
    url = os.environ.get("AMSG_URL", "")
    token = os.environ.get("AMSG_CONNECTOR_TOKEN", "")
    if not url or not token:
        print("Set AMSG_URL and AMSG_CONNECTOR_TOKEN.", file=sys.stderr)
        return 2
    if shutil.which(args.program or args.agent) is None:
        print(f"{args.program or args.agent} was not found on this host.", file=sys.stderr)
        return 1
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(_serve(build_runner(args, url, token)))
    return 0
