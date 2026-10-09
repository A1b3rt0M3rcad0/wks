"""Opaque, revocable sessions for the API-owned human workspace."""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE web_sessions (
        token_hash VARCHAR(64) PRIMARY KEY,
        client_id VARCHAR(36) NOT NULL REFERENCES clients(id),
        expires_at TIMESTAMPTZ NOT NULL);
        CREATE INDEX ix_web_sessions_client_id ON web_sessions(client_id);
        CREATE INDEX ix_web_sessions_expires_at ON web_sessions(expires_at);""")


def downgrade():
    op.execute("DROP TABLE web_sessions")
