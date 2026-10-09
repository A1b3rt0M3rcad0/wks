# ADR 0003 — Cursor opaco e watermark por namespace

Status: aceito. Geração global exporia atividade de outros tenants; watermark escalar por
vários namespaces permitiria inserir publicação tardia com contador menor. Cursors guardam
snapshot dos contadores por namespace no PostgreSQL, com hash de caller/query/filter e TTL.
O cliente recebe UUID assinado, não IDs de scopes ou offsets editáveis. GC remove snapshots
expirados. ACL/revogação continuam sendo avaliadas a cada página, mesmo com snapshot antigo.
