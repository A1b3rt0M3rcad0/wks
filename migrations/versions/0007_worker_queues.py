"""Route existing and new processing jobs to independently deployed workers."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE processing_runs ADD COLUMN queue VARCHAR(20) NOT NULL DEFAULT 'native';
        UPDATE processing_runs r SET queue = CASE
            WHEN v.media_type LIKE 'audio/%' OR v.media_type LIKE 'video/%' THEN 'media'
            WHEN r.config->>'extraction_profile' = 'docling' AND
                (v.media_type = 'application/pdf' OR v.media_type LIKE '%openxmlformats%') THEN 'docling'
            ELSE 'native' END FROM source_versions v WHERE r.source_version_id = v.id;
        ALTER TABLE processing_runs ADD CONSTRAINT processing_runs_queue_check
            CHECK (queue IN ('native', 'docling', 'media'));
        CREATE INDEX ix_processing_runs_queue_claim ON processing_runs (queue, status, created_at)
            WHERE status IN ('queued', 'retry_wait', 'running');""")


def downgrade():
    op.execute(
        "DROP INDEX ix_processing_runs_queue_claim; ALTER TABLE processing_runs DROP COLUMN queue"
    )
