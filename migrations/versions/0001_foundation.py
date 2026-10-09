"""Durable sources, scoped publications, lexical index, jobs and grants.

Revision ID: 0001
"""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    op.execute(Path(__file__).with_name("schema_0001.sql").read_text())
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE TEXT SEARCH CONFIGURATION wks_portuguese (COPY = portuguese)")
    op.execute(
        "ALTER TEXT SEARCH CONFIGURATION wks_portuguese ALTER MAPPING FOR hword, hword_part, word WITH unaccent, portuguese_stem"
    )
    op.execute("CREATE TEXT SEARCH CONFIGURATION wks_english (COPY = english)")
    op.execute(
        "ALTER TEXT SEARCH CONFIGURATION wks_english ALTER MAPPING FOR hword, hword_part, word WITH unaccent, english_stem"
    )
    op.execute("CREATE SEQUENCE wks_index_generation")
    # Published original facts and representations must not be overwritten.
    op.execute("""
    CREATE FUNCTION wks_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'Published representation is immutable'; END $$;
    CREATE TRIGGER representation_immutable BEFORE UPDATE ON representations
    FOR EACH ROW EXECUTE FUNCTION wks_immutable();
    CREATE FUNCTION wks_version_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.committed AND (
         NEW.original_blob_id IS DISTINCT FROM OLD.original_blob_id OR
         NEW.checksum IS DISTINCT FROM OLD.checksum OR
         NEW.byte_size IS DISTINCT FROM OLD.byte_size OR
         NEW.source_id IS DISTINCT FROM OLD.source_id OR
         NEW.namespace_id IS DISTINCT FROM OLD.namespace_id OR
         NOT NEW.committed) THEN
        RAISE EXCEPTION 'Committed source version is immutable';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER version_immutable BEFORE UPDATE ON source_versions
    FOR EACH ROW EXECUTE FUNCTION wks_version_immutable();
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade refused: restore a coordinated backup instead")
