# ADR 0001 — Recuperação exclusivamente textual

Status: aceito. O WKS usa PostgreSQL Full-Text Search com GIN, idiomas, ranking léxico e
proveniência. Não instala pgvector, embeddings de recuperação, bancos vetoriais, HNSW/IVF
ou grafos inferidos. OCR/ASR/layout usam modelos somente para extração; não geram índices
de similaridade de mídia. Mudança de mecanismo requer decisão futura explícita.
