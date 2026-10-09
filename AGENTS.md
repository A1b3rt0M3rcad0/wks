# Working on WKS

Use the existing checkout; cloud tasks are already isolated. Do not create a worktree unless
the user requests one. Read README and docs/ARCHITECTURE.md before extending contracts.

Keep retrieval lexical PostgreSQL FTS; no vector infrastructure or inferred semantic graph.
Domain must not import FastAPI, SQLAlchemy, Docling, MCP or consumer SDKs. HTTP/MCP invoke
shared Service methods; never bypass authorization in a transport. New read paths must check
source state and exact delegated version, including binary/resources access.

Use uv.lock frozen. `make lint` and `make test` exercise real PostgreSQL in a dedicated test
database. Preserve distinctions between passed/skipped/unrun profile checks. Never print
tokens, environment values or source payloads in logs. Migrations are append-only; schema
0001 is frozen SQL. Do not destructively downgrade immutable source history.

Lipo/Woobe changes are outside the user-confirmed scope of this delivery. Their integration
must be explicitly authorized in a separate task. No claim of external model vision/audio
without provider-specific evidence.

Read ROADMAP.md and packages/README.md before modifying package boundaries. Keep wks-api
dependent on wks-core only; core must not import API or transport frameworks. Frontend
assets live in the API wheel; follow docs/ui-workspace and the WOS reference design.
Node is test tooling only. Cookie mutations require CSRF and origin validation; MCP remains
Bearer-only. Run browser contracts when changing user journeys. WOS access is read-only.
