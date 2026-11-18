"""Uploads, agent files, Library queries, hiding and permanent deletion."""

from __future__ import annotations

import mimetypes
import re
import unicodedata
from pathlib import Path

from sqlalchemy import func, or_, select

from .. import schemas
from ..db.models import Artifact, ArtifactVersion, Message, MessageArtifact, Space
from ..errors import ApiError, conflict, invalid, missing
from ..storage.objects import StagedFile, sha256_file
from .base import DomainBase, later, new_id, now
from .views import artifact_ref

MAGIC: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF8", "image/gif"),
    (b"%PDF-", "application/pdf"),
    (b"RIFF", "audio/wav"),
    (b"OggS", "audio/ogg"),
    (b"ID3", "audio/mpeg"),
    (b"PK\x03\x04", "application/zip"),
]
TYPE_FILTERS = {
    "image": ("image/%",),
    "audio": ("audio/%",),
    "video": ("video/%",),
    "pdf": ("application/pdf",),
    "text": ("text/%", "application/json", "application/x-yaml", "application/xml"),
}
SAFE_NAME = re.compile(r"[\x00-\x1f\x7f]")


def clean_filename(name: str) -> str:
    """Reject traversal outright; the stored name is only ever displayed."""
    name = unicodedata.normalize("NFC", name).strip()
    if (
        not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or SAFE_NAME.search(name)
        or ".." in name.split(".")
    ):
        raise invalid("unsafe_filename", "That filename cannot be stored.")
    if name.startswith("."):
        name = name.lstrip(".") or "file"
    if len(name.encode()) > 240:
        stem, dot, ext = name.rpartition(".")
        name = f"{stem[:200]}{dot}{ext[:16]}" if dot else name[:220]
    return name


def detect_mime(path: Path, name: str, declared: str | None) -> str:
    with path.open("rb") as file:
        head = file.read(16)
    for magic, mime in MAGIC:
        if head.startswith(magic):
            if magic == b"RIFF" and b"WEBP" in head:
                return "image/webp"
            return mime
    if head[4:8] == b"ftyp":
        return "video/mp4"
    guessed, _ = mimetypes.guess_type(name)
    if name.lower().endswith((".md", ".markdown")):
        guessed = "text/markdown"
    return (guessed or declared or "application/octet-stream")[:127]


class ArtifactsMixin(DomainBase):
    def register_upload(
        self,
        staged: StagedFile,
        name: str,
        declared_mime: str | None,
        *,
        source: str = "upload",
        space_id: str | None = None,
        message_id: str | None = None,
    ) -> schemas.ArtifactRef:
        """Move bytes into place first; a metadata failure leaves a collectable object, never a broken card."""
        display = clean_filename(name)
        mime = detect_mime(staged.path, display, declared_mime)
        stored = staged.commit()
        with self.db.write() as session:
            timestamp = now()
            prior = None
            if source == "agent" and space_id:
                prior = session.scalar(
                    select(Artifact).where(
                        Artifact.origin_space_id == space_id,
                        Artifact.display_name == display,
                        Artifact.source == "agent",
                        Artifact.state == "ready",
                    )
                )
            if prior is not None:
                if prior.checksum == stored.checksum:
                    self.store.remove(stored.key)
                    return artifact_ref(prior)
                prior.version += 1
                prior.size, prior.checksum, prior.mime = stored.size, stored.checksum, mime
                prior.updated_at = timestamp
                prior.revision += 1
                artifact = prior
            else:
                artifact = Artifact(
                    id=new_id(),
                    display_name=display,
                    mime=mime,
                    size=stored.size,
                    checksum=stored.checksum,
                    origin_space_id=space_id,
                    origin_message_id=message_id,
                    source=source,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                session.add(artifact)
                session.flush()
            session.add(
                ArtifactVersion(
                    id=new_id(),
                    artifact_id=artifact.id,
                    version=artifact.version,
                    storage_key=stored.key,
                    size=stored.size,
                    checksum=stored.checksum,
                    created_at=timestamp,
                )
            )
            session.flush()
            ref = artifact_ref(artifact)
            if source == "agent":
                self.emit(session, "artifact.created", artifact.id, {"artifact": ref.model_dump()})
            return ref

    def register_bytes(self, data: bytes, name: str, *, space_id: str, message_id: str | None) -> schemas.ArtifactRef:
        staged = self.store.stage(self.settings.upload_limit)
        try:
            staged.write(data)
        except Exception:
            staged.abort()
            raise
        return self.register_upload(staged, name, None, source="agent", space_id=space_id, message_id=message_id)

    def register_path(self, path: str, *, space_id: str, message_id: str | None) -> schemas.ArtifactRef:
        """Register a file named by an agent. Only regular files under allowlisted roots, symlinks resolved."""
        roots = [root.resolve() for root in self.settings.workspace_roots]
        try:
            resolved = Path(path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise invalid("path_not_allowed", "That path is not available for registration.") from error
        if not any(resolved.is_relative_to(root) for root in roots) or not resolved.is_file():
            raise invalid("path_not_allowed", "That path is not available for registration.")
        if resolved.stat().st_size > self.settings.upload_limit:
            raise ApiError(413, "payload_too_large", "That file is over the upload limit.")
        staged = self.store.stage(self.settings.upload_limit)
        with resolved.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                staged.write(chunk)
        return self.register_upload(
            staged, resolved.name, None, source="agent", space_id=space_id, message_id=message_id
        )

    def _artifact_out(self, session, artifact: Artifact, *, detail: bool) -> schemas.ArtifactOut:  # type: ignore[no-untyped-def]
        space = session.get(Space, artifact.origin_space_id) if artifact.origin_space_id else None
        data = artifact_ref(artifact).model_dump()
        out = schemas.ArtifactOut(
            **data,
            origin_space_id=artifact.origin_space_id,
            origin_space_title=space.title if space and space.deleted_at is None else None,
            origin_message_id=artifact.origin_message_id,
            source=artifact.source,
            created_at=artifact.created_at,
            updated_at=artifact.updated_at,
        )
        if detail:
            versions = session.scalars(
                select(ArtifactVersion)
                .where(ArtifactVersion.artifact_id == artifact.id)
                .order_by(ArtifactVersion.version.desc())
            )
            out.versions = [
                {"version": v.version, "size": v.size, "checksum": v.checksum, "created_at": v.created_at}
                for v in versions
            ]
            out.references = self._references(session, artifact.id)
        return out

    def _references(self, session, artifact_id: str) -> list[dict]:  # type: ignore[no-untyped-def]
        rows = session.execute(
            select(Message, Space)
            .join(MessageArtifact, MessageArtifact.message_id == Message.id)
            .join(Space, Space.id == Message.space_id)
            .where(MessageArtifact.artifact_id == artifact_id, Space.deleted_at.is_(None))
            .order_by(Message.seq)
        ).all()
        return [
            {
                "message_id": message.id,
                "space_id": space.id,
                "space_title": space.title,
                "excerpt": message.markdown[:120],
                "created_at": message.created_at,
            }
            for message, space in rows
        ]

    def list_artifacts(
        self,
        *,
        q: str | None,
        type_filter: str | None,
        space_id: str | None,
        include_hidden: bool,
        offset: int,
        limit: int,
    ) -> schemas.ArtifactPage:
        limit = min(max(limit, 1), 100)
        with self.db.read() as session:
            # A staged upload joins Library when its message is sent.
            sent = or_(Artifact.origin_message_id.is_not(None), Artifact.source == "agent")
            query = select(Artifact).where(Artifact.state == "ready", sent)
            if not include_hidden:
                query = query.where(Artifact.hidden_at.is_(None))
            if space_id:
                query = query.where(Artifact.origin_space_id == space_id)
            if type_filter:
                if type_filter == "other":
                    for patterns in TYPE_FILTERS.values():
                        for pattern in patterns:
                            query = query.where(Artifact.mime.not_like(pattern))
                elif type_filter in TYPE_FILTERS:
                    query = query.where(or_(*[Artifact.mime.like(p) for p in TYPE_FILTERS[type_filter]]))
            if q and q.strip():
                query = query.where(func.lower(Artifact.display_name).contains(q.strip().lower()))
            rows = list(session.scalars(query.order_by(Artifact.updated_at.desc()).offset(offset).limit(limit + 1)))
            more = len(rows) > limit
            return schemas.ArtifactPage(
                items=[self._artifact_out(session, a, detail=False) for a in rows[:limit]],
                next_offset=offset + limit if more else None,
            )

    def get_artifact(self, artifact_id: str) -> schemas.ArtifactOut:
        with self.db.read() as session:
            artifact = session.get(Artifact, artifact_id)
            if artifact is None:
                raise missing("File")
            return self._artifact_out(session, artifact, detail=True)

    def artifact_blob(self, artifact_id: str, version: int | None) -> tuple[Artifact, ArtifactVersion]:
        with self.db.read() as session:
            artifact = session.get(Artifact, artifact_id)
            if artifact is None:
                raise missing("File")
            if artifact.state != "ready":
                raise ApiError(410, "file_deleted", "This file was permanently deleted.")
            row = session.scalar(
                select(ArtifactVersion).where(
                    ArtifactVersion.artifact_id == artifact_id,
                    ArtifactVersion.version == (version or artifact.version),
                )
            )
            if row is None:
                raise missing("File version")
            return artifact, row

    def set_hidden(self, artifact_id: str, hidden: bool) -> schemas.ArtifactOut:
        with self.db.write() as session:
            artifact = session.get(Artifact, artifact_id)
            if artifact is None:
                raise missing("File")
            artifact.hidden_at = now() if hidden else None
            artifact.revision += 1
            artifact.updated_at = now()
            session.flush()
            out = self._artifact_out(session, artifact, detail=False)
            self.emit(session, "artifact.updated", artifact.id, {"artifact": out.model_dump()})
            return out

    def delete_artifact(self, artifact_id: str, *, confirm: bool) -> schemas.ArtifactOut:
        """Permanent removal names its references first and leaves a tombstone in each conversation."""
        with self.db.write() as session:
            artifact = session.get(Artifact, artifact_id)
            if artifact is None:
                raise missing("File")
            if artifact.state == "deleted":
                return self._artifact_out(session, artifact, detail=False)
            references = self._references(session, artifact_id)
            if references and not confirm:
                raise conflict(
                    "artifact_referenced",
                    "This file is attached to conversations.",
                    references=references,
                )
            versions = list(session.scalars(select(ArtifactVersion).where(ArtifactVersion.artifact_id == artifact_id)))
            keys = [v.storage_key for v in versions]
            for version in versions:
                session.delete(version)
            artifact.state = "deleted"
            artifact.deleted_at = now()
            artifact.revision += 1
            artifact.updated_at = now()
            session.flush()
            out = self._artifact_out(session, artifact, detail=False)
            self.emit(session, "artifact.updated", artifact.id, {"artifact": out.model_dump()})
        for key in keys:
            self.store.remove(key)
        return out

    def remove_abandoned_uploads(self, older_than_days: int = 30) -> int:
        """Staged files whose message was never sent. Drafts can hold them for weeks, not forever."""
        cutoff = later(-older_than_days * 86400)
        with self.db.read() as session:
            ids = list(
                session.scalars(
                    select(Artifact.id).where(
                        Artifact.state == "ready",
                        Artifact.source == "upload",
                        Artifact.origin_message_id.is_(None),
                        Artifact.created_at < cutoff,
                    )
                )
            )
        for artifact_id in ids:
            self.delete_artifact(artifact_id, confirm=True)
        return len(ids)

    def known_object_keys(self) -> set[str]:
        with self.db.read() as session:
            return set(session.scalars(select(ArtifactVersion.storage_key)))

    def verify_objects(self) -> dict[str, int]:
        with self.db.read() as session:
            rows = list(session.scalars(select(ArtifactVersion)))
        bad = 0
        for row in rows:
            path = self.store.path_for(row.storage_key)
            if not path.is_file() or sha256_file(path) != row.checksum:
                bad += 1
        return {"checked": len(rows), "failed": bad}
