"""Provider contract. A connection declares what it supports instead of pretending to parity."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..db.models import AgentConnection
    from ..domain import Domain
    from ..schemas import ArtifactRef


class ProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RunSink:
    """Where a running provider writes. Deltas are batched so storage sees bounded writes."""

    def __init__(self, domain: Domain, job: dict[str, Any]) -> None:
        self.domain = domain
        self.job = job
        self.run_id: str = job["run_id"]
        self.space_id: str = job["space_id"]
        self.message_id: str = job["reply_message_id"]
        self._buffer: list[str] = []
        self._flusher: asyncio.Task[None] | None = None
        self._artifacts: list[str] = []
        self.done = False

    async def delta(self, text: str) -> None:
        self._buffer.append(text)
        if self._flusher is None or self._flusher.done():
            self._flusher = asyncio.create_task(self._flush_later())

    async def _flush_later(self) -> None:
        await asyncio.sleep(self.domain.settings.delta_flush_seconds)
        await self.flush()

    async def flush(self) -> None:
        if not self._buffer:
            return
        text, self._buffer = "".join(self._buffer), []
        await asyncio.to_thread(self.domain.append_delta, self.run_id, text)

    async def activity(self, text: str | None) -> None:
        await asyncio.to_thread(self.domain.set_activity, self.run_id, text)

    async def session(self, provider_session_id: str) -> None:
        await asyncio.to_thread(self.domain.set_provider_session, self.space_id, provider_session_id)

    async def attachment(self, artifact_id: str) -> bytes:
        def read() -> bytes:
            _, version = self.domain.artifact_blob(artifact_id, None)
            return self.domain.store.path_for(version.storage_key).read_bytes()

        return await asyncio.to_thread(read)

    async def artifact_bytes(self, name: str, data: bytes) -> ArtifactRef:
        ref = await asyncio.to_thread(
            self.domain.register_bytes, data, name, space_id=self.space_id, message_id=self.message_id
        )
        self._artifacts.append(ref.id)
        return ref

    async def artifact_path(self, path: str) -> ArtifactRef:
        ref = await asyncio.to_thread(
            self.domain.register_path, path, space_id=self.space_id, message_id=self.message_id
        )
        self._artifacts.append(ref.id)
        return ref

    async def artifact_path_within(self, path: Path, root: Path) -> ArtifactRef:
        """Registers a provider-produced file. The resolved path must stay inside the session directory."""
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root.resolve()):
            raise ProviderError("path_not_allowed", "The agent returned a file outside its working directory.")
        return await self.artifact_bytes(path.name, await asyncio.to_thread(resolved.read_bytes))

    async def complete(self, markdown: str | None = None) -> None:
        await self._drain()
        await asyncio.to_thread(self.domain.complete_run, self.run_id, markdown, self._artifacts)
        self.done = True

    async def fail(self, code: str, message: str) -> None:
        await self._drain()
        await asyncio.to_thread(self.domain.fail_run, self.run_id, code, message)
        self.done = True

    async def cancelled(self) -> None:
        await self._drain()
        await asyncio.to_thread(self.domain.confirm_cancelled, self.run_id)
        self.done = True

    async def _drain(self) -> None:
        if self._flusher is not None and not self._flusher.done():
            self._flusher.cancel()
        await self.flush()


class Provider(ABC):
    """An in-process provider runs inside the worker. A bridged provider is driven by a remote connector."""

    in_process = True

    def features(self, connection: AgentConnection) -> dict[str, Any]:
        return {"streaming": False, "cancel": False, "artifacts": False, "automations": False, "sessions": False}

    @abstractmethod
    async def run(self, connection: AgentConnection, job: dict[str, Any], sink: RunSink) -> None: ...

    async def cancel(self, connection: AgentConnection, run_id: str) -> bool:
        """Ask for termination. True means the request reached something able to act on it."""
        return False

    async def test(self, connection: AgentConnection) -> list[dict[str, Any]]:
        return [{"name": "provider", "ok": True, "detail": "Available"}]

    async def automation(self, connection: AgentConnection, command: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise ProviderError("unsupported", "This connection has no scheduler.")


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, kind: str, provider: Provider) -> None:
        self._providers[kind] = provider

    def get(self, kind: str) -> Provider | None:
        return self._providers.get(kind)

    def in_process_kinds(self) -> tuple[str, ...]:
        return tuple(k for k, p in self._providers.items() if p.in_process)
