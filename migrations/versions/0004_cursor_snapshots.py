"""Opaque durable cursors with per-namespace watermarks and simple language fallback."""

from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade():
    op.execute("""CREATE TABLE cursor_snapshots (
      id varchar(36) PRIMARY KEY, context_hash varchar(64) NOT NULL,
      payload jsonb NOT NULL, expires_at timestamptz NOT NULL)""")
    op.execute("CREATE INDEX ix_cursor_expiry ON cursor_snapshots(expires_at)")
    op.execute("CREATE TEXT SEARCH CONFIGURATION wks_simple (COPY = simple)")
    op.execute(
        "ALTER TEXT SEARCH CONFIGURATION wks_simple ALTER MAPPING FOR hword, hword_part, word WITH unaccent, simple"
    )


def downgrade():
    raise RuntimeError("Destructive downgrade refused; restore coordinated backup")
