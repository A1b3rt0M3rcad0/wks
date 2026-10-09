# Contrato HTTP v1

OpenAPI executável é `/openapi.json`; a cópia gerada fica em `openapi.json` neste diretório.
`Authorization: Bearer …` é obrigatório nas operações de domínio. Mutação externa exige
`Idempotency-Key` (máximo 180 caracteres), único por client. Mesmo payload retorna receipt;
outro payload sob a mesma chave retorna 409. Não há retries cegos de mutações no servidor.

## Fluxo

1. `POST /v1/namespaces` cria escopo pertencente ao client.
2. `POST /v1/sources` recebe `kind=text/upload/bookmark`, título e metadados. Upload declara
   filename, MIME, tamanho e SHA-256; bookmark não executa fetch.
3. `PUT /v1/uploads/{upload_id}/content` recebe streaming; comprimento e hash são verificados.
4. `POST /v1/uploads/{upload_id}/commit` verifica o objeto e retorna operation/version.
5. `GET /v1/operations/{id}` acompanha fatos, coverage, attempt, erro e representação publicada.
6. `POST /v1/search` encontra texto; `GET /v1/representations/{id}/read` lê a publicação fixada;
   `/v1/sources/{id}/outline` navega por versão/representação específica.
7. `GET /v1/assets/{id}/metadata` e `/content` entregam mídia autorizada; `/sources/{id}/versions/{v}/original`
   entrega o original. Ranges simples/suffix são suportados; ranges múltiplos são recusados.

`POST /sources/{id}/versions` cria novo original. `POST /sources/{id}/process` cria reprocessamento
idempotente. `POST /sources/{id}/capture` cria captura/recaptura explícita sob flag; retries do
worker só usam os bytes fixados. `/collections` e `/collections/{id}/sources/{s}` selecionam
fontes sem cópia. `/grants` e `/grants/{id}/revoke` controlam delegação. `/sources/{id}/revoke`
revoga acesso; `DELETE /sources/{id}` expurga bytes e derivados, preservando tombstones.

## Pesquisa

```json
{"query":"resistência paralelo","mode":"lexical","language":"pt","filters":{"collection_ids":[],"source_ids":[],"representation":"current_published"},"page_size":10,"cursor":null}
```

Filters extras são recusados. IDs de filtro nunca ampliam permissions. Leia `SEARCH.md`
para cursores/versionamento. Snippets e Markdown são dados não confiáveis para o renderer.

## Erros e limites

Erros têm `code`, `message`, `request_id`, `retryable`; detalhes de validação trazem localização
e tipo, sem valores. UUID inacessível usa 404 uniforme. Missing bearer: 401. Grant inválido/
expirado/ação não permitida: 403. Idempotência divergente: 409. Excedente: 413. Range: 416.
JSON é limitado a 2 MiB; para texto maior, use upload streaming. Byte limits/quota são
configuráveis. Processamento falho não apaga o original nem publicações válidas anteriores.

Status oferece ETag/If-None-Match. `/health/live` é liveness; `/health/ready` verifica banco,
migration esperada, FTS e storage. `/metrics` é autenticado e não usa IDs de alunos como labels.
