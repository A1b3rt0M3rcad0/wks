# Aceitação do WKS

Escopo confirmado pelo usuário: **somente WKS**. Gates relativos a alterações/deploys de
Lipo/Woobe não fazem parte desta entrega. Os contratos continuam disponíveis para integradores.
Não se converte simulação de um consumer em homologação de um runtime/modelo real.

| Critérios do plano | Evidência reproduzível neste repo |
|---|---|
| A01–A05 | `test_workflow`: tenants, streaming/SHA/ranges, interrupção, replay/concorrência, unsupported |
| A06 | Bookmark sob monkeypatch que falha se DNS/fetch for chamado |
| A07 | Português, acentos/unaccent, frases, stopwords, Unicode |
| A08 | Query/caller/filtros/TTL, keyset, watermark sob novas publicações |
| A09 | Nenhum Segment antes da publicação; trigger de imutabilidade e FKs |
| A10 | Read/outline paginados em texto de 500 seções; output limitado |
| A11–A14 | PDF misto real, texto/OCR, figuras/página, Markdown com URI, download/hash/MIME/ACL |
| A15–A16 | Workers paralelos, lease expirado, fencing, timeout e recovery |
| A17–A18 | Reprocessamento e nova versão preservam leitura histórica |
| A19–A20 | Grant/source revogados, expurgo e nenhuma ressurreição |
| A21 | Cliente MCP oficial externo contra Uvicorn e paridade HTTP |
| A22–A24 | WKS: initialize/list/call e isolamento por grant; integração dentro da Woobe excluída do escopo |
| A25 | Endpoint WKS verifica refs e revisões autorizadas; alteração do Tutor Lipo excluída |
| A26 | Collections múltiplas sem duplicar bytes; Study Lipo não é entidade WKS |
| A27 | WKS receipts/versionamento/SHA disponíveis; migrador de Material legacy Lipo excluído |
| A28 | Audit de schema/dependências: nenhum vetor/embedding de recuperação |
| A29–A30 | ASR local real e vídeo FFmpeg: texto, timestamps, frames, originais |
| A31 | Materialização até modelo externo permanece desligada; fora do WKS |
| A32 | MCP explicita mídia disponível mas não entregue ao modelo; resource com MIME correto |
| A33–A34 | SSRF, mixed DNS, redirect privado, pinning e snapshots/recaptura versionados |
| A35 | Dump + bytes restaurados em outra base, mesma busca/refs/ACL; corrupção recusada |
| A36 | OCR indisponível preserva mídia e não afeta texto independente; enrichment falho limitado |
| A37 | `scripts/benchmark.py`: 100 mil segmentos, dois tenants, p95/hardware reais |
| A38 | Dados recuperados marcados não confiáveis; documento não altera ACL |

## Comandos

```sh
uv sync --frozen --extra docling --extra asr
uv run --no-sync python scripts/download_models.py
uv run --no-sync python scripts/download_models.py --docling --destination .local/models/docling
make lint
make test
```

Os fixtures CC0 e hashes ficam em `tests/fixtures/manifest.json`. PostgreSQL é real. Storage
filesystem é real; contrato S3 é exercitado com Moto. MCP usa cliente/servidor reais, sem
mock de sessão. ASR/Docling podem ter skips explícitos se seus extras/modelos não estiverem
instalados; release de um perfil exige sua suíte habilitada, não soma skips aos passed.

Evidências e limitações qualificadas estão em `RELEASE.md` e `evidence/`. Publicação cloud,
CI remoto e deploy em produção só podem ser declarados depois de seus próprios resultados.
