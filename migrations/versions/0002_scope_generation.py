"""Namespace index counters avoid exposing cross-tenant publication activity."""

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade():
    op.execute("ALTER TABLE namespaces ADD COLUMN index_generation bigint NOT NULL DEFAULT 0")
    op.execute("""UPDATE namespaces n SET index_generation=coalesce((
      SELECT max(r.index_generation) FROM representations r WHERE r.namespace_id=n.id),0)""")


def downgrade():
    raise RuntimeError("Destructive downgrade refused; restore coordinated backup")
