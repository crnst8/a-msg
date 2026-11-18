"""Forking a conversation into a new space, and the context each agent turn receives."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..db.models import AgentConnection, Artifact, Message, MessageArtifact, SessionBinding, SourceLink, Space
from ..errors import ApiError, invalid
from .base import DomainBase, new_id, now
from .views import messages_out, space_out

TITLE_LIMIT = 60
TOOLS = [
    "list_spaces",
    "create_space",
    "rename_space",
    "set_space_icon",
    "fork_conversation",
    "register_artifact",
    "read_thread_context",
    "list_automations",
    "update_delivery_target",
]
FORMATS = {
    "version": 1,
    "markdown": "commonmark",
    "headings": [1, 2, 3],
    "features": ["bold", "italic", "lists", "quotes", "fenced_code", "tables"],
    "raw_html": False,
    "files": "artifact_ids",
    "instructions": (
        "Reply in CommonMark with headings 1-3, bold, italics, lists, quotes, fenced code and pipe tables. "
        "Do not use raw HTML. Register durable files with register_artifact so they appear as download cards. "
        "Change the app only through the provided tools; saying something happened does not make it happen."
    ),
}


def suggested_title(text: str) -> str:
    line = " ".join(text.split())
    for mark in ("#", ">", "*", "_", "`"):
        line = line.replace(mark, "")
    line = line.strip()
    if len(line) <= TITLE_LIMIT:
        return line
    return line[: TITLE_LIMIT - 1].rstrip() + "…"


class ThreadsMixin(DomainBase):
    def fork(self, space_id: str, body: schemas.ForkRequest, actor: str) -> schemas.ForkResult:
        with self.db.write() as session:
            source = self.space_or_404(session, space_id)

            def action() -> dict[str, Any]:
                return {"space_id": self._fork(session, source, body).id}

            result, duplicate = self.idempotent(
                session,
                "fork",
                body.idempotency_key,
                actor,
                {"space": space_id, **body.model_dump(exclude={"idempotency_key"})},
                action,
            )
            space = self.space_or_404(session, result["space_id"])
            return schemas.ForkResult(space=space_out(session, space), duplicate=duplicate)

    def _fork(self, session: Session, source: Space, body: schemas.ForkRequest) -> Space:
        wanted = list(dict.fromkeys(body.source_message_ids))
        rows = list(session.scalars(select(Message).where(Message.id.in_(wanted))))
        if len(rows) != len(wanted) or any(m.space_id != source.id for m in rows):
            raise invalid("source_outside_space", "Every selected message must belong to this space.")
        rows.sort(key=lambda m: m.seq)
        title = (body.title or "").strip()
        if not title:
            first_user = next((m for m in rows if m.role == "user" and m.markdown.strip()), rows[0])
            title = suggested_title(first_user.markdown) or f"From {source.title}"
        space = self.insert_space(
            session,
            title=title,
            icon=body.icon or schemas.Icon(kind="catalog", value="thread"),
            kind="thread",
            connection_id=body.agent_connection_id or source.agent_connection_id,
        )
        timestamp = now()
        for ordinal, message in enumerate(rows):
            session.add(
                SourceLink(
                    id=new_id(),
                    source_space_id=source.id,
                    source_message_id=message.id,
                    dest_space_id=space.id,
                    ordinal=ordinal,
                    role=message.role,
                    snapshot=message.markdown,
                    created_at=timestamp,
                )
            )
        excerpt, truncated = self._excerpt(rows)
        seq = self.next_message_seq(session)
        session.add(
            Message(
                id=new_id(),
                seq=seq,
                space_id=space.id,
                role="context",
                markdown=excerpt,
                status="completed",
                meta={
                    "source_space_id": source.id,
                    "source_message_ids": [m.id for m in rows],
                    "selected_count": len(rows),
                    "truncated": truncated,
                    "generated_summary": body.summary,
                },
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        session.flush()
        # Source text is unchanged; their "Continued in" links are new, so open clients need the records.
        for out in messages_out(session, rows):
            self.emit(session, "message.updated", out.id, {"message": out.model_dump()})
        return space

    def _excerpt(self, rows: list[Message]) -> tuple[str, bool]:
        """Keep the newest selected text within budget; the full snapshot stays in source_links."""
        budget = self.settings.context_budget_chars
        parts: list[str] = []
        used = 0
        truncated = False
        for message in reversed(rows):
            who = "User" if message.role == "user" else (message.author or "Agent")
            block = f"**{who}:**\n\n{message.markdown}"
            if used + len(block) > budget:
                truncated = True
                remaining = budget - used
                if remaining > 200:
                    parts.append(block[:remaining].rstrip() + "…")
                break
            parts.append(block)
            used += len(block)
        return "\n\n".join(reversed(parts)), truncated

    def read_thread_context(self, space_id: str, offset: int = 0, limit: int = 20) -> dict[str, Any]:
        limit = min(max(limit, 1), 50)
        with self.db.read() as session:
            self.space_or_404(session, space_id)
            rows = list(
                session.scalars(
                    select(SourceLink)
                    .where(SourceLink.dest_space_id == space_id)
                    .order_by(SourceLink.ordinal)
                    .offset(offset)
                    .limit(limit + 1)
                )
            )
            more = len(rows) > limit
            return {
                "items": [
                    {"ordinal": r.ordinal, "role": r.role, "message_id": r.source_message_id, "markdown": r.snapshot}
                    for r in rows[:limit]
                ],
                "next_offset": offset + limit if more else None,
            }

    def turn_context(
        self, session: Session, space: Space, request: Message, connection: AgentConnection
    ) -> dict[str, Any]:
        binding = session.scalar(select(SessionBinding).where(SessionBinding.space_id == space.id))
        attached = session.scalars(
            select(Artifact)
            .join(MessageArtifact, MessageArtifact.artifact_id == Artifact.id)
            .where(MessageArtifact.message_id == request.id)
            .order_by(MessageArtifact.ordinal)
        )
        context: dict[str, Any] = {
            "app": "a-msg",
            "space": {"id": space.id, "title": space.title, "kind": space.kind},
            "agent": connection.name,
            "text": request.markdown,
            "request_message_id": request.id,
            "artifacts": [{"id": a.id, "name": a.display_name, "mime": a.mime, "size": a.size} for a in attached],
            "formats": FORMATS,
            "tools": TOOLS,
            "working_directory": binding.working_directory if binding else None,
            "provider_session_id": binding.provider_session_id if binding else None,
            "access": binding.access if binding else None,
        }
        previous = session.scalar(
            select(Message.id).where(
                Message.space_id == space.id, Message.role.in_(("user", "agent")), Message.seq < request.seq
            )
        )
        seed = session.scalar(select(Message).where(Message.space_id == space.id, Message.role == "context"))
        if seed is not None and previous is None:
            meta = seed.meta or {}
            context["source_context"] = {
                "source_space_id": meta.get("source_space_id"),
                "selected_source_ids": meta.get("source_message_ids", []),
                "excerpt": seed.markdown,
                "truncated": bool(meta.get("truncated")),
                "generated_summary": meta.get("generated_summary"),
            }
        return context

    def set_provider_session(self, space_id: str, provider_session_id: str) -> None:
        with self.db.write() as session:
            binding = session.scalar(select(SessionBinding).where(SessionBinding.space_id == space_id))
            if binding is not None and binding.provider_session_id != provider_session_id:
                binding.provider_session_id = provider_session_id
                binding.updated_at = now()

    # Tool wrappers route through the same methods as the UI. The run binds them to the caller.
    def _tool_space(self, run_id: str) -> str:
        with self.db.read() as session:
            from ..db.models import Run

            run = session.get(Run, run_id)
            if run is None:
                raise ApiError(403, "unknown_run", "Tools need an active run.")
            return run.space_id
