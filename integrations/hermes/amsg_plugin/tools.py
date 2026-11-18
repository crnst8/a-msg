"""a-msg tools. Each call is bound to the run behind the current chat; the service enforces the scope."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from .client import ACTIVE_RUNS, error_text, headers, settings

log = logging.getLogger(__name__)


def _chat_id() -> str:
    from gateway.session_context import get_session_env

    return get_session_env("HERMES_SESSION_CHAT_ID", "")


def _available() -> bool:
    """Hermes caches this during warm-up, outside any session, so it reflects configuration only.

    `_call` refuses a tool that has no active a-msg run, and the service enforces the run's scope.
    """
    url, token = settings()
    return bool(url and token)


def _call(name: str, arguments: dict[str, Any]) -> str:
    run_id = ACTIVE_RUNS.get(_chat_id())
    if run_id is None:
        return json.dumps({"error": "This tool works only during an a-msg conversation turn."})
    url, token = settings()
    try:
        response = httpx.post(
            f"{url}/v1/connector/tools/{name}",
            headers=headers(token),
            json={"run_id": run_id, "arguments": arguments},
            timeout=45.0,
        )
    except httpx.HTTPError as error:
        return json.dumps({"error": f"a-msg is not reachable: {error}"})
    if response.status_code >= 300:
        return json.dumps({"error": error_text(response)})
    return response.text


def _allowed_path(path: str) -> Path | None:
    """Agents may name files only under configured roots, after symlinks resolve."""
    roots = [Path(p).expanduser().resolve() for p in os.environ.get("AMSG_WORKSPACE_ROOTS", "").split(":") if p]
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except OSError:
        return None
    if resolved.is_file() and any(resolved.is_relative_to(root) for root in roots):
        return resolved
    return None


def _register_artifact(arguments: dict[str, Any]) -> str:
    run_id = ACTIVE_RUNS.get(_chat_id())
    if run_id is None:
        return json.dumps({"error": "This tool works only during an a-msg conversation turn."})
    resolved = _allowed_path(str(arguments.get("path", "")))
    if resolved is None:
        return json.dumps({"error": "That path is not available for registration."})
    url, token = settings()
    with resolved.open("rb") as handle:
        response = httpx.post(
            f"{url}/v1/connector/uploads",
            params={"run_id": run_id, "attach": "true"},
            headers=headers(token),
            files={"file": (resolved.name, handle)},
            timeout=600.0,
        )
    if response.status_code >= 300:
        return json.dumps({"error": error_text(response)})
    artifact = response.json()
    return json.dumps({"artifact_id": artifact["id"], "name": artifact["display_name"]})


STRING = {"type": "string"}
TOOLS: dict[str, tuple[str, dict[str, Any], list[str]]] = {
    "list_spaces": ("List a-msg spaces with their IDs.", {}, []),
    "create_space": (
        "Create an a-msg space. Returns its durable ID.",
        {"title": STRING, "icon": {"type": "string", "description": "Catalog icon name such as umbrella or server"}},
        ["title"],
    ),
    "rename_space": ("Rename an a-msg space.", {"space_id": STRING, "title": STRING}, ["space_id", "title"]),
    "set_space_icon": ("Set a space's icon.", {"space_id": STRING, "icon": STRING}, ["space_id", "icon"]),
    "fork_conversation": (
        "Continue selected messages from this space in a new thread. The original stays unchanged.",
        {
            "source_message_ids": {"type": "array", "items": STRING},
            "title": STRING,
            "icon": STRING,
            "summary": {"type": "string", "description": "Optional summary, shown labelled as generated"},
        },
        [],
    ),
    "register_artifact": ("Attach a local file to this reply and add it to Library.", {"path": STRING}, ["path"]),
    "read_thread_context": (
        "Read the full messages this thread was created from, one page at a time.",
        {"offset": {"type": "integer"}, "limit": {"type": "integer"}},
        [],
    ),
    "list_automations": ("List scheduled jobs linked to spaces.", {}, []),
    "update_delivery_target": (
        "Send a scheduled job's results to a different space.",
        {"automation_id": STRING, "space_id": STRING},
        ["automation_id", "space_id"],
    ),
}


def _handler(name: str):  # type: ignore[no-untyped-def]
    def handle(arguments: dict[str, Any] | None = None, **_: Any) -> str:
        arguments = arguments or {}
        return _register_artifact(arguments) if name == "register_artifact" else _call(name, arguments)

    return handle


def register_tools(ctx: Any) -> None:
    for name, (description, properties, required) in TOOLS.items():
        parameters: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            parameters["required"] = required
        ctx.register_tool(
            name=name,
            toolset="amsg",
            schema={"name": name, "description": description, "parameters": parameters},
            handler=_handler(name),
            check_fn=_available,
            description=description,
            emoji="🟫",
        )
