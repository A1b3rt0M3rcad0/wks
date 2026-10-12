# Stabilization implementation evidence

Scope: WKS only. Apache-2.0 was selected by the owner. External homologation,
AWS certification and Lipo/Woobe runtime integration were explicitly removed
from this delivery by the owner; they are not claimed as verified.

Baseline: `a570aac7ed9bb950e7f3f9aae33628fa5f6821bb` / v0.3.0.
The original environment completed **106 passed, 0 skipped** in 452.35 seconds.
The retained application volume was backed up before schema changes; application
schema remains unchanged while implementation uses `wks_stabilization_test`.

Functional results during implementation:

- Authorization, metadata, accounts, existing workflow and maintenance: 48 passed.
- Post-snapshot deletion, grant revocation and account recovery: 7 passed, including
  restore into a separate PostgreSQL database. Deleted data stayed inaccessible;
  old password/recovery token and revoked grant remained rejected.
- Full exports and privacy regressions: 10 passed. Markdown/text/JSON/original
  downloads included the last paragraph beyond read pagination; bundle manifest
  SHA-256 matched every relative file. Unauthorized and revoked access failed.
- Native format fixtures: 15 passed; XML entity rejection and row-budget partial
  coverage included. Fixtures are synthetic and are not accuracy certification.
- Python SDK HTTP conformance: 3 passed. Go SDK: 4 tests passed with race detector.
  Tests cover network ambiguity, pagination, checksums, redirect rejection and
  resume from the confirmed server offset. Server-side resumable transport and
  full SDK-to-server qualification are still being implemented.

Evidence commands and final candidate results will be consolidated after the
remaining features are complete. This file is a progress record, not a claim
that the 1.0.0 plan is already delivered.

Later functional results:

- Native ingestion + parser + lexical regressions with seccomp enabled: 23 passed.
- Action/schema namespace policies: 2 passed.
- Recovery epochs, passwords and post-snapshot privacy: 20 passed.
- MCP read/write discovery, shared receipts and trusted context: 5 passed.
- Standalone CLI real HTTP workflow/manifests + Python SDK streamed original: 2 passed.
- Resumable transfer regressions: 5 passed; abort, expiry, empty object, duplicate
  offset and final SHA-256 tested. Large-file envelope testing is still pending.
- S3 multipart behavior using Moto: 2 passed, including immutable completion and
  abortion after failed parts. This is functional adapter testing, not AWS certification.
- Incremental GC and directory resume: 2 passed; live originals survived orphan
  quarantine/deletion. Storage/SSRF/immutable-port contracts: 14 passed.
