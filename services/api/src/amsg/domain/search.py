"""Bounded full-text search across messages, file names and space titles."""

from __future__ import annotations

import re

from sqlalchemy import func, select, text

from .. import schemas
from ..db.models import Space
from .base import DomainBase

TOKEN = re.compile(r"\w+", re.UNICODE)
LIMIT = 40


def fts_query(query: str) -> str | None:
    words = TOKEN.findall(query)
    if not words:
        return None
    return " ".join(f'"{word}"*' for word in words[:12])


class SearchMixin(DomainBase):
    def search(self, query: str, *, space_id: str | None, type_filter: str | None, limit: int) -> schemas.SearchResult:
        limit = min(max(limit, 1), LIMIT)
        match = fts_query(query)
        items: list[schemas.SearchHit] = []
        if match is None:
            return schemas.SearchResult(query=query, items=[])
        with self.db.read() as session:
            if type_filter in (None, "space") and space_id is None:
                like = f"%{query.strip().lower()}%"
                spaces = session.scalars(
                    select(Space)
                    .where(Space.deleted_at.is_(None), func.lower(Space.title).like(like))
                    .order_by(Space.ordinal)
                    .limit(8)
                )
                items += [
                    schemas.SearchHit(type="space", id=s.id, space_id=s.id, title=s.title, excerpt="") for s in spaces
                ]
            if type_filter in (None, "message"):
                rows = session.execute(
                    text(
                        """SELECT m.id, m.space_id, s.title, m.created_at,
                                  snippet(message_fts, 0, char(2), char(3), '…', 18) AS excerpt
                           FROM message_fts
                           JOIN messages m ON m.seq = message_fts.rowid
                           JOIN spaces s ON s.id = m.space_id
                           WHERE message_fts MATCH :match AND s.deleted_at IS NULL
                             AND (:space_id IS NULL OR m.space_id = :space_id)
                           ORDER BY m.seq DESC LIMIT :limit"""
                    ),
                    {"match": match, "space_id": space_id, "limit": limit},
                ).all()
                items += [
                    schemas.SearchHit(
                        type="message",
                        id=row.id,
                        space_id=row.space_id,
                        title=row.title,
                        excerpt=row.excerpt,
                        created_at=row.created_at,
                    )
                    for row in rows
                ]
            if type_filter in (None, "artifact") and space_id is None:
                rows = session.execute(
                    text(
                        """SELECT a.id, a.display_name, a.origin_space_id, a.created_at
                           FROM artifact_fts
                           JOIN artifacts a ON a.id = artifact_fts.artifact_id
                           WHERE artifact_fts MATCH :match AND a.state = 'ready' AND a.hidden_at IS NULL
                           LIMIT 10"""
                    ),
                    {"match": match},
                ).all()
                items += [
                    schemas.SearchHit(
                        type="artifact",
                        id=row.id,
                        space_id=row.origin_space_id,
                        title=row.display_name,
                        excerpt="",
                        created_at=row.created_at,
                    )
                    for row in rows
                ]
        return schemas.SearchResult(query=query, items=items)
