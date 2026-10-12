"""Bounded fuzzy candidates and rebuildable vectors without repeated source titles."""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE EXTENSION IF NOT EXISTS pg_trgm;
        CREATE INDEX ix_segments_trigram ON search_segments USING gin(text gin_trgm_ops);
        ALTER TABLE search_segments ADD COLUMN simple_vector tsvector GENERATED ALWAYS AS
            (to_tsvector('wks_simple'::regconfig,normalize(text,NFC))) STORED;
        CREATE INDEX ix_segments_simple_fts ON search_segments USING gin(simple_vector);
        UPDATE search_segments seg SET search_vector=setweight(to_tsvector(
            CASE WHEN seg.locale='en' THEN 'wks_english' WHEN seg.locale='pt' THEN 'wks_portuguese' ELSE 'wks_simple' END::regconfig,
            normalize(seg.text,NFC)), CASE WHEN seg.origin_kind='metadata' THEN 'A' ELSE 'D' END::\"char\");
        INSERT INTO source_metadata_revisions(source_id,revision,namespace_id,value,generation,created_at)
            SELECT s.id,s.revision,s.namespace_id,jsonb_build_object('title',s.title,'description',s.description,'tags',s.tags,'metadata',s.metadata,'origin','client'),n.index_generation,now()
            FROM sources s JOIN namespaces n ON n.id=s.namespace_id WHERE s.deleted_at IS NULL
            ON CONFLICT DO NOTHING;""")


def downgrade():
    raise RuntimeError("Forward-only migration: canonical search and metadata history retained")
