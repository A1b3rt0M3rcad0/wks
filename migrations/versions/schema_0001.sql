CREATE TABLE clients (
	name VARCHAR(200) NOT NULL,
	token_hash VARCHAR(64) NOT NULL,
	role VARCHAR(20) NOT NULL,
	audience VARCHAR(100) NOT NULL,
	active BOOLEAN NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name),
	UNIQUE (token_hash)
);

CREATE TABLE audit_events (
	actor VARCHAR(36) NOT NULL,
	action VARCHAR(100) NOT NULL,
	resource VARCHAR(36) NOT NULL,
	outcome VARCHAR(30) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE namespaces (
	client_id VARCHAR(36) NOT NULL,
	title VARCHAR(200) NOT NULL,
	external_ref VARCHAR(200),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (client_id, external_ref),
	FOREIGN KEY(client_id) REFERENCES clients (id)
);

CREATE INDEX ix_namespaces_client_id ON namespaces (client_id);

CREATE TABLE scope_grants (
	issuer VARCHAR(36) NOT NULL,
	caller_binding VARCHAR(36) NOT NULL,
	audience VARCHAR(100) NOT NULL,
	source_version_ids JSONB NOT NULL,
	allowed_actions JSONB NOT NULL,
	purpose VARCHAR(200) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE,
	scope_hash VARCHAR(64) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(issuer) REFERENCES clients (id),
	FOREIGN KEY(caller_binding) REFERENCES clients (id)
);

CREATE TABLE idempotency_receipts (
	client_id VARCHAR(36) NOT NULL,
	operation_key VARCHAR(250) NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	result JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (client_id, operation_key),
	FOREIGN KEY(client_id) REFERENCES clients (id)
);

CREATE TABLE sources (
	namespace_id VARCHAR(36) NOT NULL,
	kind VARCHAR(30) NOT NULL,
	title VARCHAR(500) NOT NULL,
	description TEXT NOT NULL,
	tags JSONB NOT NULL,
	metadata JSONB NOT NULL,
	external_uri TEXT,
	state VARCHAR(30) NOT NULL,
	availability VARCHAR(40) NOT NULL,
	current_version_id VARCHAR(36),
	deleted_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (id, namespace_id),
	FOREIGN KEY(namespace_id) REFERENCES namespaces (id)
);

CREATE INDEX ix_sources_namespace_id ON sources (namespace_id);

CREATE TABLE collections (
	namespace_id VARCHAR(36) NOT NULL,
	title VARCHAR(200) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (id, namespace_id),
	FOREIGN KEY(namespace_id) REFERENCES namespaces (id)
);

CREATE INDEX ix_collections_namespace_id ON collections (namespace_id);

CREATE TABLE blob_objects (
	namespace_id VARCHAR(36) NOT NULL,
	storage_key TEXT NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	size BIGINT NOT NULL,
	mime VARCHAR(200) NOT NULL,
	provider VARCHAR(30) NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (id, namespace_id),
	FOREIGN KEY(namespace_id) REFERENCES namespaces (id),
	UNIQUE (storage_key)
);

CREATE INDEX ix_blob_objects_namespace_id ON blob_objects (namespace_id);

CREATE TABLE outbox_events (
	namespace_id VARCHAR(36) NOT NULL,
	type VARCHAR(100) NOT NULL,
	payload JSONB NOT NULL,
	delivered_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(namespace_id) REFERENCES namespaces (id)
);

CREATE INDEX ix_outbox_events_namespace_id ON outbox_events (namespace_id);

CREATE TABLE collection_sources (
	collection_id VARCHAR(36) NOT NULL,
	source_id VARCHAR(36) NOT NULL,
	namespace_id VARCHAR(36) NOT NULL,
	PRIMARY KEY (collection_id, source_id),
	FOREIGN KEY(collection_id, namespace_id) REFERENCES collections (id, namespace_id),
	FOREIGN KEY(source_id, namespace_id) REFERENCES sources (id, namespace_id)
);

CREATE TABLE source_versions (
	source_id VARCHAR(36) NOT NULL,
	namespace_id VARCHAR(36) NOT NULL,
	revision_no INTEGER NOT NULL,
	original_blob_id VARCHAR(36),
	original_filename VARCHAR(500),
	declared_media_type VARCHAR(200) NOT NULL,
	media_type VARCHAR(200) NOT NULL,
	checksum VARCHAR(64),
	byte_size BIGINT,
	committed BOOLEAN NOT NULL,
	current_representation_id VARCHAR(36),
	external_uri TEXT,
	captured_at TIMESTAMP WITH TIME ZONE,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (source_id, revision_no),
	UNIQUE (id, namespace_id),
	FOREIGN KEY(source_id, namespace_id) REFERENCES sources (id, namespace_id),
	FOREIGN KEY(original_blob_id, namespace_id) REFERENCES blob_objects (id, namespace_id)
);

CREATE INDEX ix_source_versions_namespace_id ON source_versions (namespace_id);

CREATE INDEX ix_source_versions_source_id ON source_versions (source_id);

CREATE TABLE upload_sessions (
	source_version_id VARCHAR(36) NOT NULL,
	expected_sha VARCHAR(64) NOT NULL,
	expected_size BIGINT NOT NULL,
	status VARCHAR(20) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	staged_blob_id VARCHAR(36),
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(source_version_id) REFERENCES source_versions (id),
	FOREIGN KEY(staged_blob_id) REFERENCES blob_objects (id)
);

CREATE TABLE processing_runs (
	namespace_id VARCHAR(36) NOT NULL,
	source_version_id VARCHAR(36) NOT NULL,
	status VARCHAR(30) NOT NULL,
	intent_key VARCHAR(200) NOT NULL,
	config JSONB NOT NULL,
	config_digest VARCHAR(64) NOT NULL,
	processor_digest VARCHAR(64) NOT NULL,
	lease_token VARCHAR(36),
	owner VARCHAR(100),
	lease_generation INTEGER NOT NULL,
	deadline TIMESTAMP WITH TIME ZONE,
	heartbeat TIMESTAMP WITH TIME ZONE,
	attempt INTEGER NOT NULL,
	next_retry_at TIMESTAMP WITH TIME ZONE,
	published_representation_id VARCHAR(36),
	coverage JSONB NOT NULL,
	error_code VARCHAR(100),
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (source_version_id, config_digest, processor_digest, intent_key),
	FOREIGN KEY(source_version_id, namespace_id) REFERENCES source_versions (id, namespace_id)
);

CREATE INDEX ix_processing_runs_status ON processing_runs (status);

CREATE INDEX ix_processing_runs_namespace_id ON processing_runs (namespace_id);

CREATE TABLE processing_stages (
	run_id VARCHAR(36) NOT NULL,
	name VARCHAR(100) NOT NULL,
	attempt INTEGER NOT NULL,
	state VARCHAR(30) NOT NULL,
	facts JSONB NOT NULL,
	duration_seconds DOUBLE PRECISION,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES processing_runs (id)
);

CREATE INDEX ix_processing_stages_run_id ON processing_stages (run_id);

CREATE TABLE representations (
	namespace_id VARCHAR(36) NOT NULL,
	source_version_id VARCHAR(36) NOT NULL,
	processing_run_id VARCHAR(36) NOT NULL,
	structure JSONB NOT NULL,
	markdown TEXT NOT NULL,
	coverage JSONB NOT NULL,
	warnings JSONB NOT NULL,
	availability VARCHAR(40) NOT NULL,
	producer JSONB NOT NULL,
	artifact_digest VARCHAR(64) NOT NULL,
	index_generation BIGINT NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (id, namespace_id),
	FOREIGN KEY(source_version_id, namespace_id) REFERENCES source_versions (id, namespace_id),
	UNIQUE (processing_run_id),
	FOREIGN KEY(processing_run_id) REFERENCES processing_runs (id)
);

CREATE INDEX ix_representations_namespace_id ON representations (namespace_id);

CREATE TABLE assets (
	namespace_id VARCHAR(36) NOT NULL,
	representation_id VARCHAR(36) NOT NULL,
	blob_id VARCHAR(36) NOT NULL,
	kind VARCHAR(30) NOT NULL,
	locator JSONB NOT NULL,
	caption TEXT,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(representation_id, namespace_id) REFERENCES representations (id, namespace_id),
	FOREIGN KEY(blob_id, namespace_id) REFERENCES blob_objects (id, namespace_id)
);

CREATE INDEX ix_assets_representation_id ON assets (representation_id);

CREATE TABLE search_segments (
	namespace_id VARCHAR(36) NOT NULL,
	representation_id VARCHAR(36) NOT NULL,
	ordinal INTEGER NOT NULL,
	block_refs JSONB NOT NULL,
	asset_refs JSONB NOT NULL,
	text TEXT NOT NULL,
	origin_kind VARCHAR(30) NOT NULL,
	locale VARCHAR(20) NOT NULL,
	locator JSONB NOT NULL,
	checksum VARCHAR(64) NOT NULL,
	search_vector TSVECTOR NOT NULL,
	id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (representation_id, ordinal),
	FOREIGN KEY(representation_id, namespace_id) REFERENCES representations (id, namespace_id)
);

CREATE INDEX ix_segments_fts ON search_segments USING gin (search_vector);

CREATE INDEX ix_search_segments_representation_id ON search_segments (representation_id);

CREATE INDEX ix_search_segments_namespace_id ON search_segments (namespace_id);
