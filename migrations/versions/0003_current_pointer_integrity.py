"""Database-enforced current pointers and bounded blob/upload metadata."""

from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade():
    op.execute(
        "ALTER TABLE source_versions ADD CONSTRAINT version_source_identity UNIQUE (id,source_id,namespace_id)"
    )
    op.execute(
        "ALTER TABLE representations ADD CONSTRAINT representation_version_identity UNIQUE (id,source_version_id,namespace_id)"
    )
    op.execute("""ALTER TABLE sources ADD CONSTRAINT source_current_version_fk
      FOREIGN KEY (current_version_id,id,namespace_id) REFERENCES source_versions(id,source_id,namespace_id)
      DEFERRABLE INITIALLY DEFERRED""")
    op.execute("""ALTER TABLE source_versions ADD CONSTRAINT version_current_representation_fk
      FOREIGN KEY (current_representation_id,id,namespace_id) REFERENCES representations(id,source_version_id,namespace_id)
      DEFERRABLE INITIALLY DEFERRED""")
    op.execute("ALTER TABLE blob_objects ADD CONSTRAINT blob_size_positive CHECK (size>=0)")
    op.execute(
        "ALTER TABLE upload_sessions ADD CONSTRAINT upload_size_positive CHECK (expected_size>=0)"
    )
    op.execute(
        "ALTER TABLE source_versions ADD CONSTRAINT version_revision_positive CHECK (revision_no>0)"
    )


def downgrade():
    raise RuntimeError("Destructive downgrade refused; restore coordinated backup")
