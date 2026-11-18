"""a-msg platform plugin. Importing this package does nothing beyond defining `register`."""

from __future__ import annotations


def register(ctx) -> None:
    from .activity import on_tool_end, on_tool_start
    from .adapter import register as register_adapter
    from .tools import register_tools

    register_tools(ctx)
    register_adapter(ctx)
    ctx.register_hook("pre_tool_call", on_tool_start)
    ctx.register_hook("post_tool_call", on_tool_end)


__all__ = ["register"]
