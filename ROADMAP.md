# Entrega WKS

Escopo confirmado: somente `A1b3rt0M3rcad0/wks`. Lipo/Woobe não são modificados.
O plano original permanece em `docs/IMPLEMENTATION_PLAN.md`; sua aceitação dentro do
escopo WKS está em `docs/ACCEPTANCE.md`.

## Implementado

- Fontes privadas, originais/derivados, versões imutáveis e uploads idempotentes.
- PostgreSQL FTS lexical, outline/read paginados, refs fixadas e downloads autorizados.
- HTTP/MCP pelo mesmo Service, grants, revogação, expurgo, audit/outbox e métricas.
- Worker com leases/fencing, timeouts, recovery, OCR, Docling, ASR e vídeo sob configuração.
- Backup coordenado, restore/reindex, storage filesystem/S3 e controles de captura.
- Pacotes `wks-core` e `wks-api`, com dependência unidirecional, conforme o padrão WOS.
- Frontend `/app/`: resumo, catálogo, busca, versões, mídia, coleções, upload e layout mobile.
- Sessões HttpOnly, CSRF/origem, expiração/logout e invalidação após restore.
- Testes reais de browser, screenshots e instruções de reprodução.
- Cadastro da primeira conta, username/senha e recuperação com token de uso único.
- Release SemVer com CI reutilizável, tags imutáveis, GitHub Release e GHCR amd64/arm64.

## Critérios de encerramento desta atualização

A reorganização e o frontend só são considerados entregues quando lint, suíte PostgreSQL,
jornadas Chromium, wheel com assets e Compose HTTP/MCP estiverem verificados e o código
publicado no GitHub. Resultados dessa atualização estão em
`docs/verification-2026-10-08-wos-alignment.md`; resultados anteriores em `docs/RELEASE.md`.

## Fora do escopo confirmado

Alterações e homologação de consumidores Lipo/Woobe, provider multimodal externo,
implantação em produção e qualificação de infraestrutura S3 real precisam de um deployment
ou tarefa próprios. O uso de WOS é somente como referência de código/design, em leitura.
Não são pendências de implementação do serviço WKS entregue neste repositório.
