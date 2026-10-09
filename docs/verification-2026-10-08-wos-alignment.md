# Entrega: organização e frontend conforme o WOS

Data do usuário: 2026-10-08 (America/Sao_Paulo).
Código de aplicação: `59610a2c3e0c36da775e6a6d5709b4665e604c7e`.

O WKS foi reorganizado em `packages/wks-core` e `packages/wks-api`, com o núcleo sem
importar transportes. O frontend `/app/` segue o padrão visual e de navegação do WOS,
com biblioteca, resumo, busca, coleções, upload, leitura, versões históricas e mídia.
O WOS foi consultado somente em leitura, no commit
`0fb77fe400e10dba6fb519287a86fb45a5edcdb2`; nenhuma alteração foi feita nele.

## Verificações executadas

- **76 testes passaram**, sem falhas, erros ou skips, em **511,70 segundos**.
- PostgreSQL 17 real, migração `0005`, isolamento entre clientes, grants, FTS, cursores,
  concorrência, worker, revogação/expurgo e backup/restore.
- OCR, PDF nativo, Docling com modelos fixados, Office, ASR CPU e vídeo com FFmpeg.
- MCP oficial sobre Uvicorn real, paridade com HTTP e autorização negativa.
- Sessão de navegador opaca, hash no banco, CSRF/origem, expiração, logout, desativação
  de client e invalidação das sessões após restaurar backup.
- **4 jornadas reais no Chromium passaram**, sem falhas, skips ou retries de testes:
  catálogo de 26 fontes paginado; busca/leitura/original/mídia; mutações e versões
  históricas com texto não confiável; mobile/upload/logout; e retomada após perda da
  resposta HTTP sem criar contexto duplicado.
- Ruff, formatação Python, Prettier e sintaxe JavaScript passaram.
- Wheels dos dois pacotes foram construídos; o wheel da API inclui todos os assets web.

As quatro jornadas agrupam os fluxos descritos; não se trata de uma contagem de cinco testes.
Dois avisos de depreciação vieram de dependências (Starlette/httpx e configuração de OCR
do Docling). Nenhuma asserção foi desabilitada. Os waits do navegador aguardam publicação
real pelo worker e conclusão das consultas, sem atrasos fixos ou resultados simulados.

Resultados por caso e versões estão em
[`evidence/wos-alignment-validation.json`](evidence/wos-alignment-validation.json).
[Capturas e reprodução](ui-workspace/README.md) mostram a aplicação real e explicam a
base isolada e as dependências. A suíte Python usa outra base descartável, terminada em
`_test`, sem truncar a base da aplicação.

## Execução empacotada

O registro do Compose está em
[`evidence/wos-alignment-runtime.json`](evidence/wos-alignment-runtime.json).
A imagem `82aac667561e2272b6afd499573e63bbf61b52cfa112e49edf9698f0c002e763`
rodou como UID 10001: API e PostgreSQL saudáveis, worker em execução e migração concluída
com exit 0 no schema `0005`. Upload, checksum, publicação, busca, leitura, download e
expurgo passaram por HTTP. O cliente MCP oficial inicializou, listou as seis Tools e
consultou FTS. Chromium também verificou login, resumo real e logout nessa imagem.
Os assets servidos e o hash conjunto da aplicação/migrações coincidiram com o checkout.
A imagem padrão contém o frontend e o perfil native/OCR; os modelos opcionais de
Docling/ASR/vídeo foram qualificados na instalação Python do host. Habilitar esses perfis
em containers exige os extras e montagens documentados.

O código da aplicação, assets e migrações tem SHA-256 conjunto
`332087159bb92944326c1a51b00387e3fadeb01628ceb81ae9e3d44bf5117428`.
Commits posteriores de capturas e evidências não alteram esses arquivos.

## Reprodução e publicação

Use `README.md`, `packages/README.md` e `docs/ui-workspace/README.md` para iniciar e testar.
O OpenAPI fica em `packages/wks-api/openapi.json`; regenere com
`uv run --no-sync python scripts/export_openapi.py`. O workflow inclui as jornadas Chromium
com API, PostgreSQL e worker reais, sem traces/vídeos com credenciais.

Os resultados acima são da execução local/cloud desta entrega. CI remoto, provider
multimodal externo, S3 real e implantação em produção não são declarados como homologados.
A entrega e as instruções reutilizáveis de setup se restringem ao WKS. A publicação de um
snapshot do ambiente é uma ação própria do produto; salvar sua configuração não executa
nem publica esse snapshot e não comprova restauração em uma tarefa nova.
