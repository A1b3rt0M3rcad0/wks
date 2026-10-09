# Workers independentes

A API autentica, recebe e preserva a fonte, cria a versão e enfileira o job. O worker
executa extração/OCR/transcrição fora do processo HTTP e publica representação, assets e
segmentos FTS de forma atômica. O worker permite controlar CPU, memória e número de
réplicas sem aumentar as réplicas da API.

A fila é persistida em `processing_runs.queue` na criação do job:

| Conteúdo e política | Fila |
| --- | --- |
| Áudio/vídeo, inclusive quando a modalidade está desabilitada | `media` |
| PDF/Office com `WKS_EXTRACTION_PROFILE=docling` | `docling` |
| Demais formatos, bookmarks e arquivos sem decoder | `native` |

Cada claim filtra a fila antes de `FOR UPDATE SKIP LOCKED`. Um worker de outra fila não
incrementa tentativas nem recupera um lease expirado desse job. Réplicas da mesma fila
compartilham jobs com leases/fencing, sem publicar duas vezes. Sem worker correspondente,
o job aguarda na fila. Adicionar réplicas da API não executa processamento.

## Instalação e execução

```sh
# API isolada, sem decoders:
uv sync --frozen --no-dev --package wks-api
uv run --no-sync wks api

# Em outro checkout/ambiente virtual ou container, com o mesmo banco/object store:
uv sync --frozen --no-dev --package wks-worker-native
uv run --no-sync wks-worker-native

# Perfis adicionais, em ambientes separados:
uv sync --frozen --no-dev --package wks-worker-docling --extra models
uv run --no-sync wks-worker-docling
uv sync --frozen --no-dev --package wks-worker-media --extra asr
uv run --no-sync wks-worker-media
```

FFmpeg é necessário para mídia; Tesseract com `por+eng` para OCR. Os Dockerfiles incluem
essas ferramentas nos workers. A API não precisa delas. Todos os processos devem usar o
mesmo PostgreSQL e o mesmo object store; em filesystem compartilhe o volume de objetos.
Migrações são executadas uma vez pelo serviço `migrate`, antes dos workers e da API.

## Compose e escala

```sh
docker compose up --build -d
docker compose up -d --no-build --scale worker=3 --scale worker-media=2
# Após configurar política e modelos:
WKS_EXTRACTION_PROFILE=docling docker compose --profile docling up --build -d
```

`worker` executa somente o nativo; `worker-media` executa somente mídia. Ambos sobem por
padrão, inclusive para registrar cobertura explícita quando ASR/vídeo estão desabilitados.
Docling é opt-in. Os limites de CPU/memória são individuais. Ao escalar com filesystem,
use o volume compartilhado; em máquinas distintas configure S3 para todos os processos.

Para construir a imagem local de mídia com ASR, defina
`WKS_MEDIA_WORKER_EXTRA="--extra asr"`. A imagem publicada de mídia já inclui ASR;
a imagem publicada de Docling já inclui os decoders/model runtime. Pesos não ficam
embutidos nas imagens: baixe os modelos verificados com `scripts/download_models.py`.

Os paths são parte da configuração imutável do job. Monte modelos no mesmo path absoluto
na API (para registrar o hash) e no worker correspondente (para executar e verificar o hash).
API lê somente os arquivos de pin; não carrega os modelos. Por exemplo, um override Compose:

```yaml
services:
  api:
    volumes:
      - ./.local/models:/models:ro
    environment:
      WKS_DOCLING_ARTIFACTS_PATH: /models/docling
      WKS_ASR_MODEL_PATH: /models/asr-tiny
  worker-docling:
    volumes:
      - ./.local/models/docling:/models/docling:ro
  worker-media:
    volumes:
      - ./.local/models/asr-tiny:/models/asr-tiny:ro
```

Defina `WKS_DOCLING_ARTIFACTS_PATH=/models/docling`, `WKS_ASR_MODEL_PATH=/models/asr-tiny`,
`WKS_ASR_ENABLED=true`, `WKS_VIDEO_ENABLED=true` no environment do Compose conforme necessário.
O nativo não valida ou acessa os pesos de outros perfis. Docling com falha preserva fallback
nativo e avisa sobre cobertura; isso não conta como qualificação do modelo. A release
qualifica Docling sem fallback e ASR real com fixtures e pesos verificados, em rede desabilitada.

## Upgrade

A migração append-only 0007 adiciona e preenche as filas dos jobs existentes com base
no MIME e na política armazenada. Ela preserva configurações, digests, tentativas, leases,
fontes e contas. Ao atualizar de 0.2.0, pare os workers antigos, execute a migração e suba
os novos workers e a API. Não mantenha um worker antigo sem filtro de fila em execução.
Backup/restore da versão atual exige schema 0007. Guarde um backup anterior com sua versão
correspondente antes de atualizar. Restore invalida sessões e preserva filas e histórico.

`wks-worker --once` executa no máximo um job elegível; `--reconcile` reconcilia expurgos e
relata objetos órfãos. Esses comandos pertencem ao runtime, sem dependência da API.
