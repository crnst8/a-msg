from typing import Any


def build_tool_preview(tool_name: str, args: dict[str, Any], max_len: int = 40) -> str | None:
    value = args.get("command") or args.get("query") or args.get("path")
    return str(value)[:max_len] if value else None
