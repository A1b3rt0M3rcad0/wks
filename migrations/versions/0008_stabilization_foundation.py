"""Expand canonical projections, scoped grants and recoverable operational records."""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE representations ADD COLUMN schema_version VARCHAR(20) NOT NULL DEFAULT '1.0';
        ALTER TABLE representations ADD COLUMN created_txid BIGINT NOT NULL DEFAULT txid_current();
        ALTER TABLE sources ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE sources ADD COLUMN external_ref VARCHAR(250);
        CREATE UNIQUE INDEX source_external_ref_unique ON sources(namespace_id,external_ref) WHERE external_ref IS NOT NULL;
        ALTER TABLE namespaces ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE namespaces ADD COLUMN state VARCHAR(20) NOT NULL DEFAULT 'active';
        ALTER TABLE namespaces ADD COLUMN policy JSONB NOT NULL DEFAULT '{}'::jsonb;
        ALTER TABLE collections ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE collections ADD COLUMN state VARCHAR(20) NOT NULL DEFAULT 'active';
        ALTER TABLE scope_grants ADD COLUMN namespace_id VARCHAR(36) REFERENCES namespaces(id);
        ALTER TABLE scope_grants ADD COLUMN collection_id VARCHAR(36) REFERENCES collections(id);
        ALTER TABLE idempotency_receipts ADD COLUMN authorization_scope_hash VARCHAR(64);
        ALTER TABLE blob_objects ADD COLUMN location JSONB NOT NULL DEFAULT '{}'::jsonb;
        ALTER TABLE upload_sessions ADD COLUMN received_offset BIGINT NOT NULL DEFAULT 0;
        ALTER TABLE upload_sessions ADD COLUMN protocol VARCHAR(20) NOT NULL DEFAULT 'whole';""")
    # Frozen foundation DDL; future ORM changes cannot rewrite upgrades.
    op.execute("""CREATE TABLE blob_operations (
	namespace_id VARCHAR(36) NOT NULL, 
	kind VARCHAR(30) NOT NULL, 
	resource_id VARCHAR(250) NOT NULL, 
	storage_key TEXT NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	size BIGINT NOT NULL, 
	mime VARCHAR(200) NOT NULL, 
	provider VARCHAR(30) NOT NULL, 
	state VARCHAR(30) NOT NULL, 
	blob_id VARCHAR(36), 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	error_code VARCHAR(100), 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (namespace_id, kind, resource_id), 
	FOREIGN KEY(namespace_id) REFERENCES namespaces (id), 
	UNIQUE (storage_key)
);
CREATE INDEX ix_blob_operations_namespace_id ON blob_operations (namespace_id);
CREATE INDEX ix_blob_operations_state ON blob_operations (state);
CREATE TABLE backup_snapshots (
	state VARCHAR(30) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	watermark VARCHAR(36), 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE backup_pins (
	snapshot_id VARCHAR(36) NOT NULL, 
	blob_id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (snapshot_id, blob_id), 
	FOREIGN KEY(snapshot_id) REFERENCES backup_snapshots (id) ON DELETE CASCADE, 
	FOREIGN KEY(blob_id) REFERENCES blob_objects (id)
);
CREATE TABLE representation_blocks (
	representation_id VARCHAR(36) NOT NULL, 
	ordinal INTEGER NOT NULL, 
	block_id VARCHAR(36) NOT NULL, 
	kind VARCHAR(40) NOT NULL, 
	parent_id VARCHAR(36), 
	value JSONB NOT NULL, 
	checksum VARCHAR(64) NOT NULL, 
	PRIMARY KEY (representation_id, ordinal), 
	UNIQUE (representation_id, block_id), 
	FOREIGN KEY(representation_id) REFERENCES representations (id) ON DELETE CASCADE
);
CREATE TABLE export_artifacts (
	namespace_id VARCHAR(36) NOT NULL, 
	representation_id VARCHAR(36) NOT NULL, 
	format VARCHAR(40) NOT NULL, 
	options_digest VARCHAR(64) NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	blob_id VARCHAR(36), 
	warnings JSONB NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (representation_id, format, options_digest), 
	FOREIGN KEY(namespace_id) REFERENCES namespaces (id), 
	FOREIGN KEY(representation_id) REFERENCES representations (id) ON DELETE CASCADE
);
CREATE INDEX ix_export_artifacts_namespace_id ON export_artifacts (namespace_id);
CREATE TABLE upload_parts (
	upload_id VARCHAR(36) NOT NULL, 
	"offset" BIGINT NOT NULL, 
	size BIGINT NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	journal_id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (upload_id, "offset"), 
	FOREIGN KEY(upload_id) REFERENCES upload_sessions (id) ON DELETE CASCADE, 
	FOREIGN KEY(journal_id) REFERENCES blob_operations (id)
);
CREATE TABLE client_credentials (
	client_id VARCHAR(36) NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	description VARCHAR(200) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	last_used_at TIMESTAMP WITH TIME ZONE, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(client_id) REFERENCES clients (id), 
	UNIQUE (token_hash)
);
CREATE INDEX ix_client_credentials_client_id ON client_credentials (client_id);
CREATE TABLE worker_heartbeats (
	owner VARCHAR(100) NOT NULL, 
	queues JSONB NOT NULL, 
	capabilities JSONB NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (owner)
);
CREATE INDEX ix_worker_heartbeats_updated_at ON worker_heartbeats (updated_at);
CREATE TABLE retention_sweeps (
	id VARCHAR(100) NOT NULL, 
	cursor TEXT, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE orphan_candidates (
	storage_key TEXT NOT NULL, 
	observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (storage_key)
);
CREATE TABLE destructive_preflights (
	client_id VARCHAR(36) NOT NULL, 
	action VARCHAR(40) NOT NULL, 
	resource_id VARCHAR(36) NOT NULL, 
	revision INTEGER NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	consumed BOOLEAN NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(client_id) REFERENCES clients (id)
);
CREATE TABLE source_metadata_revisions (
	source_id VARCHAR(36) NOT NULL, 
	revision INTEGER NOT NULL, 
	namespace_id VARCHAR(36) NOT NULL, 
	value JSONB NOT NULL, 
	generation BIGINT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (source_id, revision), 
	FOREIGN KEY(source_id) REFERENCES sources (id), 
	FOREIGN KEY(namespace_id) REFERENCES namespaces (id)
);
CREATE INDEX ix_source_metadata_revisions_namespace_id ON source_metadata_revisions (namespace_id);
CREATE TABLE action_windows (
	key VARCHAR(64) NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	attempts INTEGER NOT NULL, 
	PRIMARY KEY (key)
);""")
    op.execute("""INSERT INTO representation_blocks(representation_id,ordinal,block_id,kind,parent_id,value,checksum)
        SELECT r.id, b.ordinality-1,b.value->>'id',b.value->>'type',b.value->>'parent_id',
            b.value || jsonb_build_object('ordinal', b.ordinality-1),
            encode(sha256(convert_to(b.value::text,'UTF8')),'hex')
        FROM representations r CROSS JOIN LATERAL jsonb_array_elements(r.structure) WITH ORDINALITY b(value,ordinality);
        CREATE FUNCTION wks_block_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='INSERT' THEN
            IF (SELECT created_txid FROM representations WHERE id=NEW.representation_id) <> txid_current() THEN
              RAISE EXCEPTION 'Published blocks are immutable'; END IF;
            RETURN NEW;
          ELSIF TG_OP='DELETE' AND NOT EXISTS(
            SELECT 1 FROM representations r JOIN source_versions v ON v.id=r.source_version_id
            JOIN sources s ON s.id=v.source_id WHERE r.id=OLD.representation_id AND s.deleted_at IS NULL) THEN
            RETURN OLD;
          END IF;
          RAISE EXCEPTION 'Published blocks are immutable';
        END $$;
        CREATE TRIGGER representation_blocks_immutable BEFORE INSERT OR UPDATE OR DELETE ON representation_blocks
          FOR EACH ROW EXECUTE FUNCTION wks_block_immutable();
        CREATE INDEX ix_export_blob ON export_artifacts(blob_id);
        CREATE INDEX ix_blob_op_reservation ON blob_operations(namespace_id,state);
        CREATE INDEX ix_events_namespace_cursor ON outbox_events(namespace_id,created_at,id);
        INSERT INTO client_credentials(id,created_at,client_id,token_hash,description)
          SELECT id,created_at,id,token_hash,'initial credential' FROM clients;""")


def downgrade():
    raise RuntimeError("Destructive downgrade refused; restore a coordinated backup")
