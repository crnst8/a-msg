"""Immutable object store beneath the service data directory. Addressed by generated keys only."""

from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

CHUNK = 1024 * 1024


class TooLarge(Exception):
    pass


@dataclass(slots=True)
class Stored:
    key: str
    size: int
    checksum: str


class StagedFile:
    """Incoming bytes in a temporary file; hashing and the size limit apply as chunks arrive."""

    def __init__(self, store: ObjectStore, limit: int) -> None:
        self._store = store
        self._limit = limit
        self.path = store.tmp_dir / f"{uuid.uuid4().hex}.part"
        self._file: BinaryIO = self.path.open("xb")
        self._hash = hashlib.sha256()
        self.size = 0

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > self._limit:
            self.abort()
            raise TooLarge
        self._hash.update(chunk)
        self._file.write(chunk)

    def commit(self) -> Stored:
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        key = uuid.uuid4().hex
        target = self._store.path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.path, target)
        return Stored(key=key, size=self.size, checksum=self._hash.hexdigest())

    def abort(self) -> None:
        if not self._file.closed:
            self._file.close()
        self.path.unlink(missing_ok=True)


class ObjectStore:
    def __init__(self, object_dir: Path, tmp_dir: Path) -> None:
        self.object_dir = object_dir
        self.tmp_dir = tmp_dir
        self._retention = threading.RLock()

    def path_for(self, key: str) -> Path:
        if not key.isalnum():
            raise ValueError("invalid object key")
        return self.object_dir / key[:2] / key

    def stage(self, limit: int) -> StagedFile:
        return StagedFile(self, limit)

    def put_bytes(self, data: bytes) -> Stored:
        staged = self.stage(len(data) + 1)
        staged.write(data)
        return staged.commit()

    def exists(self, key: str) -> bool:
        return self.path_for(key).is_file()

    def remove(self, key: str) -> None:
        with self._retention:
            self.path_for(key).unlink(missing_ok=True)

    @contextmanager
    def retention_hold(self) -> Iterator[None]:
        """Block object removal while a backup reads the manifest and copies files."""
        with self._retention:
            yield

    def read_range(self, key: str, start: int, end: int) -> Iterator[bytes]:
        remaining = end - start + 1
        with self.path_for(key).open("rb") as file:
            file.seek(start)
            while remaining > 0:
                chunk = file.read(min(CHUNK, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
                yield chunk

    def sweep_temporary(self, max_age_seconds: int = 86400) -> int:
        cutoff = time.time() - max_age_seconds
        removed = 0
        for path in self.tmp_dir.glob("*.part"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def sweep_orphans(self, known_keys: set[str], grace_seconds: int = 86400) -> int:
        """Remove committed objects whose metadata insert never happened."""
        cutoff = time.time() - grace_seconds
        removed = 0
        with self._retention:
            for path in self.object_dir.glob("*/*"):
                if path.name not in known_keys and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1
        return removed


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
