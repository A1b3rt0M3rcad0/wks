"""Username/password accounts and authentication throttles owned by the API."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE web_accounts (
        client_id VARCHAR(36) PRIMARY KEY REFERENCES clients(id),
        username VARCHAR(64) NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        recovery_token_hash VARCHAR(64) NOT NULL UNIQUE,
        created_at TIMESTAMPTZ NOT NULL);
        CREATE TABLE web_auth_attempts (
        key_hash VARCHAR(64) PRIMARY KEY,
        window_started TIMESTAMPTZ NOT NULL,
        attempts INTEGER NOT NULL);
        CREATE INDEX ix_web_auth_attempts_window_started ON web_auth_attempts(window_started);
        DELETE FROM web_sessions;""")


def downgrade():
    op.execute("DROP TABLE web_auth_attempts; DROP TABLE web_accounts")
