"""full-text search over messages and artifact metadata

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE VIRTUAL TABLE message_fts USING fts5(body, tokenize='unicode61 remove_diacritics 2')"
    )
    op.execute(
        "CREATE VIRTUAL TABLE artifact_fts USING fts5("
        "artifact_id UNINDEXED, name, tokenize='unicode61 remove_diacritics 2')"
    )
    op.execute(
        """CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages
        WHEN new.role IN ('user', 'agent') AND new.markdown <> '' BEGIN
          INSERT INTO message_fts(rowid, body) VALUES (new.seq, new.markdown);
        END"""
    )
    op.execute(
        """CREATE TRIGGER messages_fts_update AFTER UPDATE OF markdown, status ON messages
        WHEN new.role IN ('user', 'agent') AND new.status <> 'running' BEGIN
          DELETE FROM message_fts WHERE rowid = old.seq;
          INSERT INTO message_fts(rowid, body) SELECT new.seq, new.markdown WHERE new.markdown <> '';
        END"""
    )
    op.execute(
        """CREATE TRIGGER messages_fts_delete AFTER DELETE ON messages BEGIN
          DELETE FROM message_fts WHERE rowid = old.seq;
        END"""
    )
    op.execute(
        """CREATE TRIGGER artifacts_fts_insert AFTER INSERT ON artifacts BEGIN
          INSERT INTO artifact_fts(artifact_id, name) VALUES (new.id, new.display_name);
        END"""
    )
    op.execute(
        """CREATE TRIGGER artifacts_fts_delete AFTER UPDATE OF state ON artifacts
        WHEN new.state = 'deleted' BEGIN
          DELETE FROM artifact_fts WHERE artifact_id = old.id;
        END"""
    )


def downgrade() -> None:
    for trigger in (
        "messages_fts_insert",
        "messages_fts_update",
        "messages_fts_delete",
        "artifacts_fts_insert",
        "artifacts_fts_delete",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.execute("DROP TABLE IF EXISTS message_fts")
    op.execute("DROP TABLE IF EXISTS artifact_fts")
