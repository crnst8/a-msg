"""Shared domain plumbing: time, IDs, transactional events and idempotency."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import SCHEMA_VERSION
from ..config import Settings
from ..db import Database
from ..db.models import ActionRequest, Event, Message, Meta, Space
from ..errors import conflict, missing
from ..storage.objects import ObjectStore

T = TypeVar("T")
ACTIVE_RUN_STATES = ("running", "cancel_requested")
TERMINAL_RUN_STATES = ("completed", "failed", "cancelled", "delivery_uncertain")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def later(seconds: float) -> str:
    moment = datetime.now(UTC) + timedelta(seconds=seconds)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


def request_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class Notifier:
    """Wakes in-process waiters after a commit. Thread-safe; carries no data."""

    def __init__(self) -> None:
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        with self._lock:
            self._callbacks.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._callbacks:
                    self._callbacks.remove(callback)

        return unsubscribe

    def notify(self) -> None:
        with self._lock:
            callbacks = list(self._callbacks)
        for callback in callbacks:
            callback()


class DomainBase:
    settings: Settings
    db: Database
    store: ObjectStore
    notifier: Notifier

    def emit(self, session: Session, type_: str, entity_id: str | None, payload: dict[str, Any]) -> int:
        """Allocate an event in the caller's transaction, so state and event commit together."""
        event = Event(
            schema_version=SCHEMA_VERSION,
            type=type_,
            entity_id=entity_id,
            payload=payload,
            created_at=now(),
        )
        session.add(event)
        session.flush()
        return event.seq

    def next_message_seq(self, session: Session) -> int:
        return (session.scalar(select(func.max(Message.seq))) or 0) + 1

    def space_or_404(self, session: Session, space_id: str) -> Space:
        space = session.get(Space, space_id)
        if space is None or space.deleted_at is not None:
            raise missing("Space")
        return space

    def idempotent(
        self,
        session: Session,
        operation: str,
        key: str | None,
        actor: str,
        request: Any,
        action: Callable[[], dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        """Run ``action`` once per key. A repeat returns the stored result; changed arguments conflict."""
        if key is None:
            return action(), False
        digest = request_hash(request)
        existing = session.scalar(
            select(ActionRequest).where(ActionRequest.operation == operation, ActionRequest.idempotency_key == key)
        )
        if existing is not None:
            if existing.request_hash != digest:
                raise conflict("idempotency_conflict", "This idempotency key was used with different arguments.")
            return existing.result or {}, True
        result = action()
        session.add(
            ActionRequest(
                id=new_id(),
                operation=operation,
                actor=actor,
                idempotency_key=key,
                request_hash=digest,
                result=result,
                status="completed",
                created_at=now(),
            )
        )
        return result, False

    def meta_get(self, session: Session, key: str) -> str | None:
        row = session.get(Meta, key)
        return row.value if row else None

    def meta_set(self, session: Session, key: str, value: str) -> None:
        row = session.get(Meta, key)
        if row:
            row.value = value
        else:
            session.add(Meta(key=key, value=value))
