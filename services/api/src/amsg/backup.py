"""Backup and restore. The database goes through the SQLite backup API; objects are copied by manifest."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .config import Settings
from .db.migrate import snapshot
from .storage.objects import ObjectStore, sha256_file


class RestoreFailed(RuntimeError):
    pass


def create_backup(settings: Settings, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    store = ObjectStore(settings.object_dir, settings.tmp_dir)
    # Hold removal so nothing named in the database snapshot disappears before it is copied.
    with store.retention_hold():
        snapshot(settings.db_path, destination / "amsg.db")
        database = sqlite3.connect(destination / "amsg.db")
        rows = database.execute("SELECT storage_key, size, checksum FROM artifact_versions").fetchall()
        messages = database.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        database.close()
        objects = []
        for key, size, checksum in rows:
            source = store.path_for(key)
            target = destination / "objects" / key[:2] / key
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            objects.append({"key": key, "size": size, "sha256": checksum})
    manifest = {
        "format": 1,
        "service_version": __version__,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "message_count": messages,
        "objects": objects,
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return destination


def verify_backup(path: Path) -> dict:
    manifest = json.loads((path / "manifest.json").read_text())
    failures = []
    for entry in manifest["objects"]:
        file = path / "objects" / entry["key"][:2] / entry["key"]
        if not file.is_file() or sha256_file(file) != entry["sha256"]:
            failures.append(entry["key"])
    database = sqlite3.connect(path / "amsg.db")
    count = database.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    integrity = database.execute("PRAGMA integrity_check").fetchone()[0]
    database.close()
    if count != manifest["message_count"]:
        failures.append("message_count")
    if integrity != "ok":
        failures.append("integrity_check")
    return {"failures": failures, "messages": count, "objects": len(manifest["objects"])}


def restore_backup(source: Path, settings: Settings) -> dict:
    """Restore only into a fresh data directory. Device tokens are revoked and automations marked stale."""
    if settings.db_path.exists():
        raise RestoreFailed(f"{settings.data_dir} already holds a database; restore needs a fresh directory.")
    report = verify_backup(source)
    if report["failures"]:
        raise RestoreFailed(f"backup failed verification: {report['failures']}")
    settings.ensure_dirs()
    shutil.copy2(source / "amsg.db", settings.db_path)
    copied = source / "objects"
    if copied.is_dir():
        shutil.copytree(copied, settings.object_dir, dirs_exist_ok=True)
    database = sqlite3.connect(settings.db_path)
    with database:
        stamp = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        database.execute("UPDATE device_credentials SET revoked_at = ? WHERE revoked_at IS NULL", (stamp,))
        database.execute("UPDATE agent_connections SET state = 'disconnected' WHERE kind IN ('hermes', 'bridge')")
        # A restored copy must not act as a second live scheduler destination until it is re-paired.
        database.execute("UPDATE automation_links SET last_synced_at = NULL")
        database.execute("UPDATE meta SET value = lower(hex(randomblob(16))) WHERE key = 'service_id'")
    database.close()
    return report
