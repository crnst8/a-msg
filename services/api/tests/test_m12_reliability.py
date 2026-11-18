"""M12: backup, restore, scale and measured budgets."""

from __future__ import annotations

import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from amsg import schemas
from amsg.api.app import create_app
from amsg.backup import RestoreFailed, create_backup, restore_backup, verify_backup
from amsg.config import Settings
from amsg.db.migrate import MigrationFailed, SchemaTooNew, current_revision, upgrade
from amsg.domain import Domain

from .conftest import Harness, make_harness


def populated(h: Harness) -> dict:
    run = h.send(h.general, "health").json()["run"]
    h.wait_run(run["id"])
    return h.get(f"/v1/spaces/{h.general}/messages").json()


def test_backup_restore_verifies_counts_and_every_checksum(h: Harness, tmp_path: Path) -> None:
    populated(h)
    source = tmp_path / "backup"
    create_backup(h.settings, source)
    report = verify_backup(source)
    assert report["failures"] == []
    assert report["messages"] == 2 and report["objects"] == 1

    fresh = Settings(data_dir=tmp_path / "restored", provider="fake", dev_mode=True)
    restore_backup(source, fresh)
    restored = Domain(fresh)
    assert restored.verify_objects() == {"checked": 1, "failed": 0}
    with restored.db.read() as session:
        general = restored.general_space_id(session)
    assert len(restored.list_messages(general, before=None, limit=50).items) == 2
    restored.close()


def test_restore_revokes_credentials_and_gets_a_new_identity(h: Harness, tmp_path: Path) -> None:
    populated(h)
    original_id = h.domain.service_id()
    source = tmp_path / "backup"
    create_backup(h.settings, source)
    fresh = Settings(data_dir=tmp_path / "restored", provider="fake", dev_mode=True)
    restore_backup(source, fresh)
    restored = Domain(fresh)
    assert restored.authenticate(h.token) is None
    assert restored.service_id() != original_id
    restored.close()


def test_restore_marks_automations_unsynced_so_a_copy_cannot_look_live(h: Harness, tmp_path: Path) -> None:
    with h.domain.db.read() as session:
        connection = h.domain.primary_connection_id(session)
    h.domain.sync_automations(connection, [schemas.ConnectorJobReport(provider_job_id="j", name="J")])
    source = tmp_path / "backup"
    create_backup(h.settings, source)
    fresh = Settings(data_dir=tmp_path / "restored", provider="fake", dev_mode=True)
    restore_backup(source, fresh)
    restored = Domain(fresh)
    assert restored.list_automations()[0].stale is True
    restored.close()


def test_restore_refuses_an_existing_data_directory(h: Harness, tmp_path: Path) -> None:
    source = tmp_path / "backup"
    create_backup(h.settings, source)
    with pytest.raises(RestoreFailed):
        restore_backup(source, h.settings)


def test_restore_refuses_a_tampered_object(h: Harness, tmp_path: Path) -> None:
    populated(h)
    source = tmp_path / "backup"
    create_backup(h.settings, source)
    victim = next((source / "objects").glob("*/*"))
    victim.write_bytes(b"tampered")
    with pytest.raises(RestoreFailed):
        restore_backup(source, Settings(data_dir=tmp_path / "restored"))
    assert not (tmp_path / "restored" / "amsg.db").exists()


def test_retention_hold_blocks_removal_during_backup(h: Harness) -> None:
    import threading

    stored = h.domain.store.put_bytes(b"held")
    removed = threading.Event()

    def remove() -> None:
        h.domain.store.remove(stored.key)
        removed.set()

    with h.domain.store.retention_hold():
        thread = threading.Thread(target=remove)
        thread.start()
        assert not removed.wait(0.15)
        assert h.domain.store.exists(stored.key)
    thread.join(timeout=2)
    assert removed.is_set()


def test_ten_thousand_messages_page_quickly(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    h = next(harness)
    try:
        import sqlite3

        database = sqlite3.connect(h.settings.db_path)
        rows = [
            (str(uuid.uuid4()), index + 1, h.general, f"message {index} about the harbour", "2026-11-06T00:00:00.000Z")
            for index in range(10000)
        ]
        with database:
            database.executemany(
                "INSERT INTO messages (id, seq, space_id, role, markdown, status, meta, revision,"
                " created_at, updated_at) VALUES (?, ?, ?, 'agent', ?, 'completed', '{}', 1, ?, ?5)",
                rows,
            )
        database.close()
        cursor = None
        seen = 0
        timings = []
        while True:
            started = time.perf_counter()
            suffix = f"&before={cursor}" if cursor else ""
            page = h.get(f"/v1/spaces/{h.general}/messages?limit=200{suffix}").json()
            timings.append(time.perf_counter() - started)
            seen += len(page["items"])
            cursor = page["next_before"]
            if cursor is None:
                break
        assert seen == 10000
        assert max(timings) < 1.0
        started = time.perf_counter()
        assert h.get("/v1/search", params={"q": "harbour", "limit": 20}).status_code == 200
        assert time.perf_counter() - started < 1.0
    finally:
        harness.close()


def test_acceptance_budget(tmp_path: Path) -> None:
    """Service acceptance p95 under 300 ms, excluding network and inference."""
    harness = make_harness(tmp_path)
    h = next(harness)
    try:
        h.domain.settings.max_concurrent_runs = 0
        timings = []
        for _ in range(200):
            started = time.perf_counter()
            assert h.send(h.general, "x").status_code == 202
            timings.append(time.perf_counter() - started)
        p95 = statistics.quantiles(timings, n=20)[18]
        print(f"acceptance p50={statistics.median(timings) * 1000:.1f}ms p95={p95 * 1000:.1f}ms")
        assert p95 < 0.3
    finally:
        harness.close()


def test_fake_provider_cannot_activate_outside_development(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        create_app(Settings(data_dir=tmp_path, provider="fake", dev_mode=False))


def test_fake_provider_is_labelled(h: Harness) -> None:
    assert h.get("/v1/connections").json()[0]["development_only"] is True


def test_dev_seed_refuses_without_the_flag(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "amsg.cli", "dev-seed", "--data-dir", str(tmp_path)], capture_output=True, text=True
    )
    assert done.returncode == 2


def test_upgrade_backs_up_first_and_refuses_a_newer_schema(tmp_path: Path) -> None:
    from alembic import command

    from amsg.db.migrate import alembic_config

    database = tmp_path / "amsg.db"
    command.upgrade(alembic_config(database), "0001")
    backup = upgrade(database, tmp_path / "backups")
    assert backup is not None and backup.exists()
    assert current_revision(database) == "0004"

    import sqlite3

    connection = sqlite3.connect(database)
    with connection:
        connection.execute("UPDATE alembic_version SET version_num = '9999'")
    connection.close()
    with pytest.raises(SchemaTooNew):
        upgrade(database, tmp_path / "backups")


def test_failed_migration_restores_the_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from alembic import command

    from amsg.db import migrate

    database = tmp_path / "amsg.db"
    command.upgrade(migrate.alembic_config(database), "0001")
    before = database.read_bytes()

    def explode(*_a, **_k) -> None:  # type: ignore[no-untyped-def]
        database.write_bytes(b"corrupted by a failed migration")
        raise RuntimeError("boom")

    monkeypatch.setattr(migrate.command, "upgrade", explode)
    with pytest.raises(MigrationFailed) as caught:
        migrate.upgrade(database, tmp_path / "backups")
    assert caught.value.backup is not None and caught.value.backup.exists()
    assert database.read_bytes() != b"corrupted by a failed migration"
    assert len(before) > 0


def test_no_secrets_in_error_or_logs(h: Harness, tmp_path: Path) -> None:
    from amsg.logging import redact

    assert h.token not in redact(f"Authorization: Bearer {h.token}")
    assert h.token not in redact(f"token={h.token}")


def test_cancelled_error_message_does_not_expose_traces(h: Harness) -> None:
    def boom(*_a, **_k):  # type: ignore[no-untyped-def]
        raise RuntimeError("secret internal detail")

    h.domain.list_spaces = boom  # type: ignore[method-assign]
    response = h.get("/v1/spaces")
    assert response.status_code == 500
    assert "secret internal detail" not in response.text
    assert response.json()["error"]["code"] == "internal_error"
