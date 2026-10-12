"""Explicit operator identity; delegation never implies identity administration."""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE clients ADD COLUMN operator BOOLEAN NOT NULL DEFAULT false")
    op.execute("UPDATE clients SET operator=true WHERE id IN (SELECT client_id FROM web_accounts)")


def downgrade():
    raise RuntimeError("Forward-only migration: operator policy must not be silently discarded")
