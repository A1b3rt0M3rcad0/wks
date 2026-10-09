# WKS — Woobe Knowledge Service

Serviço Python independente para armazenar fontes privadas, preservar originais e assets,
publicar representações versionadas e recuperar conteúdo por **busca textual PostgreSQL**.
HTTP e MCP usam os mesmos casos de uso e a mesma autorização. API, worker, PostgreSQL e
object store funcionam sem Lipo, Woobe ou WOS.

## Entrega e escopo

Este repositório entrega o WKS. Conforme o escopo confirmado, não modifica Lipo ou Woobe.
Os contratos de integração e delegação estão documentados; homologação de um runtime/modelo
externo não é apresentada como teste executado neste projeto.

**Não há embeddings, pgvector, busca vetorial, índices híbridos ou grafo inferido.**
`tsvector` é o índice léxico nativo do PostgreSQL. Markdown é uma projeção da árvore JSON;
originais, páginas, figuras, versões e referências não são substituídos por resumos.

Inclui uploads por streaming com SHA-256 e replay idempotente, namespaces/collections,
versões imutáveis, fila PostgreSQL com leases/fencing/recovery, texto/PDF/OCR/Office/imagem,
ASR e vídeo sob flags, bookmark e captura explícita protegida contra SSRF, pesquisa lexical,
outline/read paginados, assets e downloads autenticados, grants por revisão, MCP somente
leitura, outbox, auditoria sem conteúdo, métricas e backup/restore/reindexação.

## Frontend e organização

O [workspace humano](docs/ui-workspace/README.md) está em `/app/`: biblioteca, busca,
coleções, versões, leitura e mídia, com o padrão visual do WOS. Abra a URL da API e use
a credencial do client provisionado. O navegador recebe uma sessão HttpOnly, sem guardar
a credencial em armazenamento local.

O código segue [dois pacotes](packages/README.md): `wks-core` contém domínio, casos de uso,
portas e adapters; `wks-api` contém HTTP/MCP, autenticação, contratos, servidor e frontend.
A API depende do núcleo. O WOS foi consultado somente para referência, sem alterações nele.

## Rodar com Compose

Pré-requisitos: Docker com Compose. Defaults são exclusivos de desenvolvimento local.

```sh
docker compose up --build -d
docker compose exec api wks provision-client --name local --token-file .local/local-token
docker compose exec api python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8080/health/ready").status)'
```

O token é gravado com permissão `0600`, não impresso. O comando mostra somente o ID do cliente.
A API escuta em `127.0.0.1:8080` no host. O worker é um processo separado. Em cloud com proxy,
use [as instruções de build e confiança](docs/OPERATIONS.md) antes do Compose.

## Desenvolvimento Python

Linux, Python 3.12, uv 0.12.19, PostgreSQL 17, FFmpeg e Tesseract são a combinação validada.

```sh
export UV_CACHE_DIR=/workspace/.cache/uv
uv sync --frozen --extra docling --extra asr
docker compose up -d postgres
uv run --no-sync alembic upgrade head
uv run --no-sync wks provision-client --name local --token-file .local/local-token
uv run --no-sync wks api
# Em outro processo:
uv run --no-sync wks worker
```

O perfil `native` preserva texto nativo, OCR seletivo, imagens e renderizações das páginas.
Tabelas/equações cuja estrutura não possa ser extraída permanecem acessíveis como imagem.
O perfil `docling` produz estrutura de layout/tabelas com os modelos qualificados:

```sh
uv run --no-sync python scripts/download_models.py --docling --destination .local/models/docling
uv run --no-sync python scripts/download_models.py --destination .local/models/asr-tiny
```

Os downloads usam revisões fixadas e verificam hashes LFS/git antes de concluir o manifesto.
Para ativar modalidades, configure `WKS_DOCLING_ARTIFACTS_PATH`, `WKS_ASR_MODEL_PATH`,
`WKS_ASR_ENABLED`, `WKS_VIDEO_ENABLED` e/ou `WKS_WEB_CAPTURE_ENABLED`. Em Compose, extras
exigem `WKS_BUILD_EXTRAS="--extra docling --extra asr"` e montagem dos modelos no worker.
OCR, ASR e descrições visuais têm coberturas separadas. Transcrição não implica análise de
sons não verbais; frames amostrados não implicam compreensão integral do vídeo.

## Validar

```sh
make lint
make test-unit
make test
uv run --no-sync python scripts/smoke.py --token-file .local/local-token
uv run --no-sync python scripts/benchmark.py
```

Os testes usam uma base descartável terminada em `_test`, nunca a base da aplicação.
ASR real exige o extra e o modelo local verificado; a ausência aparece como **skip**, não
como sucesso de transcrição. Docling exige o extra e artefatos para qualificação de PDF.
O benchmark usa base própria descartável, 100 mil segmentos sintéticos e dois tenants;
não mede qualidade de extração nem desempenho de produção.

Leia [aceitação](docs/ACCEPTANCE.md), [evidências](docs/RELEASE.md), [contratos HTTP](docs/API.md),
[MCP](docs/MCP.md), [segurança](docs/AUTHORIZATION.md), [processamento](docs/PROCESSING.md)
e [operação](docs/OPERATIONS.md). O plano original foi preservado em
[IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md); o escopo de entrega executado é WKS.
