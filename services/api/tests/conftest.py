from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from amsg.api.app import create_app
from amsg.config import Settings
from amsg.domain import Domain


@dataclass
class Harness:
    client: TestClient
    domain: Domain
    token: str
    settings: Settings

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, path: str, **kw):  # type: ignore[no-untyped-def]
        return self.client.get(path, headers=self.auth, **kw)

    def post(self, path: str, **kw):  # type: ignore[no-untyped-def]
        return self.client.post(path, headers=self.auth, **kw)

    def patch(self, path: str, **kw):  # type: ignore[no-untyped-def]
        return self.client.patch(path, headers=self.auth, **kw)

    def delete(self, path: str, **kw):  # type: ignore[no-untyped-def]
        return self.client.delete(path, headers=self.auth, **kw)

    @property
    def general(self) -> str:
        with self.domain.db.read() as session:
            return self.domain.general_space_id(session)

    def send(self, space_id: str, text: str = "hello", client_id: str | None = None):  # type: ignore[no-untyped-def]
        body = {"client_message_id": client_id or str(uuid.uuid4()), "markdown": text}
        return self.post(f"/v1/spaces/{space_id}/messages", json=body)

    def wait_run(self, run_id: str, *states: str, timeout: float = 20.0) -> str:
        import time

        from amsg.db.models import Run

        states = states or ("completed", "failed", "cancelled")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.domain.db.read() as session:
                state = session.get(Run, run_id).state
            if state in states:
                return state
            time.sleep(0.02)
        raise AssertionError(f"run stayed {state}")


def make_harness(data_dir: Path, **overrides) -> Iterator[Harness]:  # type: ignore[no-untyped-def]
    values = {"data_dir": data_dir, "provider": "fake", "dev_mode": True, **overrides}
    settings = Settings(**values)
    app = create_app(settings)
    domain = app.state.services.domain
    with TestClient(app, raise_server_exceptions=False) as client:
        yield Harness(client, domain, domain.issue_device_token("test"), settings)


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield from make_harness(tmp_path)
