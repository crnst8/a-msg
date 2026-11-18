"""How much a coding agent may do in a session: read only, write in its folder, or full access.

A session may choose a level up to the connection's ceiling. A runner sets its own ceiling on its own host and checks
every run against it, so the service cannot raise it.
"""

from __future__ import annotations

from typing import Any

LEVELS = ("read", "workspace", "full")
CODEX_SANDBOX = {"read": "read-only", "workspace": "workspace-write", "full": "danger-full-access"}
CLAUDE_MODE = {"read": "plan", "workspace": "acceptEdits", "full": "bypassPermissions"}
NAMES = {"read": "read only", "workspace": "workspace", "full": "full access"}


def default_level(kind: str, config: dict[str, Any]) -> str:
    """The level of the program's configured sandbox or permission mode."""
    if kind == "codex":
        mapping, value = CODEX_SANDBOX, config.get("sandbox", "workspace-write")
    else:
        mapping, value = CLAUDE_MODE, config.get("permission_mode", "acceptEdits")
    return next((level for level, setting in mapping.items() if setting == value), "workspace")


def ceiling(kind: str, config: dict[str, Any]) -> str:
    """The highest level sessions may choose. A connection on the service's host allows every level unless capped."""
    cap = config.get("max_access", "full")
    default = default_level(kind, config)
    return max(cap if cap in LEVELS else "workspace", default, key=LEVELS.index)


def allows(top: str | None, level: str) -> bool:
    return top in LEVELS and level in LEVELS and LEVELS.index(level) <= LEVELS.index(top)
