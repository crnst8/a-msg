"""Shows the tool in use on the running reply. Hermes's own progress lines would land in the reply text instead."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .client import ACTIVE_RUNS, headers, settings
from .tools import _chat_id

log = logging.getLogger(__name__)
TIMEOUT = 1.0
LIMIT = 300
LABELS = {
    "terminal": "Running",
    "execute_code": "Running code",
    "read_file": "Reading",
    "write_file": "Writing",
    "patch": "Editing",
    "search_files": "Searching files for",
    "web_search": "Searching the web for",
    "web_extract": "Reading",
    "browser_navigate": "Opening",
    "delegate_task": "Delegating",
    "memory": "Updating memory",
    "skill_view": "Reading skill",
}


def describe(tool_name: str, args: dict[str, Any]) -> str:
    from agent.display import build_tool_preview

    preview = build_tool_preview(tool_name, args, max_len=160) or ""
    label = LABELS.get(tool_name) or tool_name.replace("_", " ").capitalize()
    return f"{label} {preview}".strip()[:LIMIT]


def _report(text: str | None) -> None:
    run_id = ACTIVE_RUNS.get(_chat_id())
    if run_id is None:
        return
    url, token = settings()
    try:
        httpx.post(
            f"{url}/v1/connector/runs/{run_id}/activity", headers=headers(token), json={"text": text}, timeout=TIMEOUT
        )
    except httpx.HTTPError as error:
        log.debug("a-msg activity not delivered: %s", error)


def on_tool_start(tool_name: str = "", args: dict[str, Any] | None = None, **_: Any) -> None:
    _report(describe(tool_name, args or {}))


def on_tool_end(**_: Any) -> None:
    _report(None)
