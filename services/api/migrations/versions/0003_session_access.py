"""per-session access level for coding agents

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("session_bindings", sa.Column("access", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("session_bindings") as batch:
        batch.drop_column("access")
