# Stabilization architecture

The Core remains independent of HTTP/MCP and converters. Shared authorization and
idempotency live in `application/authorization.py`; revisioned administration,
keyset reads, lexical queries, capabilities and full exports have separate
application modules. HTTP/MCP validate public contracts and call those modules.
Worker profiles retain isolated packages and runtime queues. The Python and Go
SDKs contain no server database, object-store or converter dependencies. `wksctl`
is a separate Go module that uses the public Go SDK.

Canonical blocks are normalized by representation and ordinal. Published rows
are immutable; exact JSON pages expose a full-block checksum. Migration 0010
repairs the initial backfill checksum to match PostgreSQL's delivered JSON bytes
without changing any block content. Source metadata revisions are separate from
immutable original bytes and published representations. Search resolves metadata
at its captured generation; body vectors no longer repeat the source title.

Blob intents reserve quota in short transactions. Immutable IO and full SHA-256
verification happen before an attachment transaction. Physical deletion intents
survive removal of canonical references. Backup pins retain bytes required by an
exported PostgreSQL MVCC snapshot while the global write lock is released before
`pg_dump` and object copy. Privacy decisions live in a signed external ledger;
restore verifies objects and replays privacy decisions before removing its traffic
block marker. Account recovery revokes prior credentials and issued delegations.

Decoders execute in a temporary working directory with broker credentials removed
from the environment and dotenv loading disabled. Seccomp denies network/process
inspection syscalls; CPU, address-space and elapsed-time budgets remain enforced.
Strict mode additionally requires Landlock filesystem scopes and refuses decoding
when unavailable. This cloud kernel reports Landlock unavailable; network isolation
was exercised here, and strict-mode refusal was verified. Filesystem isolation is
not claimed on this kernel. Production deployments should choose their required
sandbox envelope explicitly and keep models/inputs separate from broker secrets.
The `seccomp` policy applies network/process isolation consistently on every host;
it does not opportunistically enable filesystem scopes. Select `strict` explicitly
when the installation requires both boundaries.

MCP mutation tools select their namespace from a grant or trusted
`X-WKS-Namespace` connection header. Source IDs narrow that context; they do not
select a tenant. Read-only legacy grants keep their original tool discovery.
Identity administration is never exposed as a model tool. Destructive actions
require a confirmation tied to caller, grant, source revision and expiry. Binary
upload remains out of band. Both transports share core receipts and authorization.
