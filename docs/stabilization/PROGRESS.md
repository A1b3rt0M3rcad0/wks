# WKS stabilization delivery evidence

Scope: WKS only. Apache-2.0 was selected by the owner. External homologation,
AWS certification, Lipo/Woobe changes and production SLO/staging certification
were explicitly removed from this delivery. They are not claimed as verified.

The reviewed functional revision is `28e9cc7e77ca9718289352b8552151d344b93edc`.
[GitHub CI](https://github.com/A1b3rt0M3rcad0/wks/actions/runs/38225972179)
completed **197 Python cases passed, zero skipped**, plus **five Chromium
journeys passed**. The Go SDK and CLI race tests passed natively on Linux,
macOS and Windows. The final documentation commit adds this evidence without
changing runtime behavior; the release workflow repeats validation at its exact
source SHA before publishing artifacts or images.

The suite includes canonical block/backfill constraints, typed public schemas,
scoped write grants, metadata revisions, current/historical references, full
exports, native XLSX/ODS/EPUB/XML/HTML/code/image fixtures, actual local
Docling/ASR/video profiles, lexical phrase/exact/fuzzy/proximity search, authorized
facets/suggestions, quotas, worker fairness, events, receipts and both SDKs plus
standalone CLI over the real public HTTP gateway. CLI declarative sync creates
revisions, handles content reversion, renames collections and resumes confirmed
upload offsets. Declarative assertions run in isolated namespaces.

Storage evidence includes immutable filesystem and Moto S3 adapter behavior,
explicit migration/import, multipart abort, paged GC, corruption/readiness,
conditional/range downloads and crash recovery across 20 injected checkpoints.
The real HTTP 1 GiB test interrupts at 50%, resumes with a new client, verifies
full SHA-256 and leaves exactly one SourceVersion. Bytes are synthetic/sparse;
this is transport qualification, not a production bandwidth claim.

Backup/restore replays post-snapshot deletion, grant/credential revocation,
password recovery and container deletion before opening traffic. Temporary
uncommitted multipart offsets are expired after restore rather than advertised
without their bytes. Committed originals are copied and verified. An isolated
upgrade from actual schema 0007 to 0015 preserved the original SHA-256, the
legacy representation and all four normalized blocks, whose checksums matched
PostgreSQL JSON bytes. The retained application upgrade preserved its five
historical Sources and did not create a human account.

The updated host development API and independent native worker passed readiness,
stream upload, durable processing, lexical search, read, original SHA-256 and
purge in a functional smoke. `/app/setup` remains `setup_required=true` for the
actual installation. The original PostgreSQL/objects volumes and schema-0007
backup were preserved. Docker VFS image builds exhausted this 32-GB cloud disk;
the selected development workflow uses host processes, while GitHub qualifies
and publishes the four release image profiles on amd64/arm64.

A clean installed API from wheels loaded exactly the packaged OpenAPI and stamped
release identity. Release packaging/verifier tests passed, including all seven
Python distributions, six standalone CLI OS/architecture archives, pinned
requirements, source, license notices, CycloneDX inventory and SHA256SUMS.
Checksums cover every distribution file. The inventory documents language
packages and optional profiles; it does not claim OS/model license or CVE scans.

The synthetic lexical corpus `wks-synthetic-lexical-v1` contains four explicit
qrels: Portuguese stemming, English phrase, Unicode history and chemistry.
Public-SDK evaluation produced Recall@10 = 1.0 and nDCG@10 = 1.0 for that corpus.
This small fixture is not an accuracy, million-segment or capacity certification.
No evidence justifies replacing PostgreSQL lexical search with BM25 or vectors.

Decoder policy `seccomp` consistently blocks network/process inspection.
`strict` explicitly adds Landlock filesystem scopes and refuses unavailable
isolation. This cloud kernel reports Landlock unavailable; filesystem isolation
is not claimed here. The native-platform CI and strict-mode boundary tests keep
that limitation explicit. Production operators select the required boundary and
keep models/inputs apart from broker secrets.

Release publication status is authoritative in the GitHub release workflow and
its immutable tag/manifest; an in-progress run is not a completed publication.

Publication completed: [v1.0.0](https://github.com/A1b3rt0M3rcad0/wks/releases/tag/v1.0.0) is public and stable.
[The exact-tag release workflow](https://github.com/A1b3rt0M3rcad0/wks/actions/runs/38227113059) succeeded at
`c6995b37ed4cbd3fb75670ceaa2267f3e1f46faf`. All **34 public assets** were downloaded and
verified against their manifest and SHA256SUMS. API and native/Docling/media
image jobs and the combined qualification gate all succeeded. The Go SDK module
tag `packages/wks-sdk-go/v1.0.0` points to the same reviewed source.
The source code is integrated in master and PR #1 is merged.
