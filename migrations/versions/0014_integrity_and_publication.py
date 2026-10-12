"""Record verified integrity and reject publication without its normalized blocks."""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE blob_integrity(blob_id VARCHAR(36) PRIMARY KEY REFERENCES blob_objects(id),status VARCHAR(30) NOT NULL,verification VARCHAR(30) NOT NULL,checked_at TIMESTAMPTZ NOT NULL DEFAULT now());
        CREATE FUNCTION wks_publication_complete() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (SELECT count(*) FROM representation_blocks WHERE representation_id=NEW.id) <> jsonb_array_length(NEW.structure) THEN
            RAISE EXCEPTION 'Publication requires every normalized canonical block';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER representation_publication_complete AFTER INSERT ON representations
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION wks_publication_complete();""")


def downgrade():
    raise RuntimeError("Forward-only: integrity and complete publication invariants retained")
