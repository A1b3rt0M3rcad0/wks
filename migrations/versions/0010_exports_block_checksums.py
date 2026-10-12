"""Lease full exports and align normalized block hashes with delivered JSON bytes."""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE export_leases (
        export_id VARCHAR(36) PRIMARY KEY REFERENCES export_artifacts(id) ON DELETE CASCADE,
        token VARCHAR(36) NOT NULL, generation INTEGER NOT NULL DEFAULT 1,
        expires_at TIMESTAMPTZ NOT NULL);
        CREATE INDEX ix_export_leases_expires_at ON export_leases(expires_at);
        LOCK TABLE representation_blocks IN ACCESS EXCLUSIVE MODE;
        ALTER TABLE representation_blocks DISABLE TRIGGER representation_blocks_immutable;
        UPDATE representation_blocks SET checksum=encode(sha256(convert_to(value::text,'UTF8')),'hex');
        ALTER TABLE representation_blocks ENABLE TRIGGER representation_blocks_immutable;""")


def downgrade():
    raise RuntimeError("Forward-only: export leases and published block checksums must be retained")
