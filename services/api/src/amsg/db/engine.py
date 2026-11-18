"""SQLite access: WAL, foreign keys, busy timeout and one serialized write path.

Writers use a single-connection engine that opens with BEGIN IMMEDIATE, so a write transaction holds the
writer slot from its first statement. Readers use a separate engine whose BEGIN gives each session one
consistent WAL snapshot. Never hold a transaction open across network or provider I/O.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

BUSY_TIMEOUT_MS = 5000


def _make_engine(path: Path, *, begin: str, pool_size: int) -> Engine:
    engine = create_engine(
        f"sqlite:///{path}",
        poolclass=QueuePool,
        pool_size=pool_size,
        max_overflow=0,
        pool_timeout=30,
        connect_args={"check_same_thread": False, "timeout": BUSY_TIMEOUT_MS / 1000},
    )

    @event.listens_for(engine, "connect")
    def on_connect(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cursor.close()

    @event.listens_for(engine, "begin")
    def on_begin(connection):  # type: ignore[no-untyped-def]
        connection.exec_driver_sql(begin)

    return engine


class TrackedSession(Session):
    """Records whether a transaction wrote anything, so an empty one wakes nobody."""


@event.listens_for(TrackedSession, "after_flush")
def _mark_changed(session: Session, _context: object) -> None:
    session.info["changed"] = True


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._writer = _make_engine(path, begin="BEGIN IMMEDIATE", pool_size=1)
        self._reader = _make_engine(path, begin="BEGIN", pool_size=8)
        self._write_lock = threading.RLock()
        self._after_commit: list[Callable[[], None]] = []

    def on_commit(self, callback: Callable[[], None]) -> None:
        self._after_commit.append(callback)

    @contextmanager
    def read(self) -> Iterator[Session]:
        with Session(self._reader, expire_on_commit=False) as session, session.begin():
            yield session

    @contextmanager
    def write(self) -> Iterator[Session]:
        with self._write_lock, TrackedSession(self._writer, expire_on_commit=False) as session:
            with session.begin():
                yield session
            changed = bool(session.info.get("changed"))
        # Waiters re-check after a wake. A commit that changed nothing must not wake them, or a
        # poll that writes on each pass would wake itself forever.
        if changed:
            for callback in self._after_commit:
                callback()

    def dispose(self) -> None:
        self._writer.dispose()
        self._reader.dispose()
