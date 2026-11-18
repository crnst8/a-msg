"""Connector HTTP access and the run currently bound to each chat. Shared by the adapter and tools."""

from __future__ import annotations

import os
from typing import Any

ACTIVE_RUNS: dict[str, str] = {}
OWNER_ID = "amsg-owner"


def settings() -> tuple[str, str]:
    from gateway.platforms._shared import get_scoped_secret

    url = (get_scoped_secret("AMSG_URL", "") or os.environ.get("AMSG_URL", "")).rstrip("/")
    token = get_scoped_secret("AMSG_CONNECTOR_TOKEN", "") or os.environ.get("AMSG_CONNECTOR_TOKEN", "")
    return url, token


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def error_text(response: Any) -> str:
    try:
        return str(response.json()["error"]["message"])
    except Exception:
        return f"a-msg returned {response.status_code}"
