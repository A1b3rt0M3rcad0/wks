"""Whitelisted metadata/tag filters retain bounded indexed candidates."""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX ix_sources_metadata_gin ON sources USING gin(metadata jsonb_path_ops); CREATE INDEX ix_sources_tags_gin ON sources USING gin(tags jsonb_path_ops)"
    )


def downgrade():
    raise RuntimeError("Forward-only metadata index migration")
