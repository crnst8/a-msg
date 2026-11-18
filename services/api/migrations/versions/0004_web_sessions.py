"""browser sign-ins are device credentials marked with the client that holds them

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("device_credentials", sa.Column("client", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("device_credentials") as batch:
        batch.drop_column("client")
