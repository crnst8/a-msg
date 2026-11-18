"""Alembic entry points. Upgrades back up the database first and refuse a newer schema than this binary."""

from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import create_engine


class SchemaTooNew(RuntimeError):
    pass


class MigrationFailed(RuntimeError):
    def __init__(self, backup: Path | None, cause: Exception) -> None:
        super().__init__(f"migration failed: {cause}")
        self.backup = backup


def migrations_dir() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "migrations"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[3] / "migrations"


def alembic_config(db_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(migrations_dir()))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def current_revision(db_path: Path) -> str | None:
    if not db_path.exists():
        return None
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        revision = MigrationContext.configure(connection).get_current_revision()
    engine.dispose()
    return revision


def head_revision(db_path: Path) -> str | None:
    return ScriptDirectory.from_config(alembic_config(db_path)).get_current_head()


def snapshot(db_path: Path, destination: Path) -> None:
    """Copy through the SQLite backup API; never copy a live WAL database file."""
    source = sqlite3.connect(db_path)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def upgrade(db_path: Path, backup_dir: Path | None = None) -> Path | None:
    script = ScriptDirectory.from_config(alembic_config(db_path))
    current = current_revision(db_path)
    head = script.get_current_head()
    if current == head:
        return None
    if current is not None:
        try:
            known = script.get_revision(current) is not None
        except CommandError:
            known = False
        if not known:
            raise SchemaTooNew(f"database schema {current} is newer than this service; install a newer build")
    backup: Path | None = None
    if current is not None and backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = backup_dir / f"pre-migration-{current}-{stamp}.db"
        snapshot(db_path, backup)
    try:
        command.upgrade(alembic_config(db_path), "head")
    except Exception as error:
        if backup:
            for suffix in ("-wal", "-shm"):
                db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
            shutil.copy2(backup, db_path)
        raise MigrationFailed(backup, error) from error
    return backup
