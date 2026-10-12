"""Preserve physical deletion intent across failed IO and backup pins."""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE blob_retirements (
        blob_id VARCHAR(36) PRIMARY KEY REFERENCES blob_objects(id),
        source_id VARCHAR(36) NOT NULL REFERENCES sources(id), state VARCHAR(30) NOT NULL DEFAULT 'pending',
        requested_at TIMESTAMPTZ NOT NULL DEFAULT now());
        CREATE INDEX ix_blob_retirements_source_id ON blob_retirements(source_id);
        CREATE INDEX ix_blob_retirements_state ON blob_retirements(state);""")


def downgrade():
    raise RuntimeError("Forward-only: durable deletion intents must survive interruptions")
