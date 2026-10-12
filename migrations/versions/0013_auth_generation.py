"""Invalidate already authenticated principals after account recovery."""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE clients ADD COLUMN auth_generation INTEGER NOT NULL DEFAULT 0")


def downgrade():
    raise RuntimeError("Forward-only: recovery epochs must not be erased")
