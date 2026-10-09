# Pacotes WKS

A organização segue a fronteira do WOS: biblioteca reutilizável e serviço independente.
O código continua Python; o WOS foi consultado somente como referência.

| Pacote | Responsabilidade |
| --- | --- |
| `wks-core` | `domain`, `application`, `ports`, `storage`, configuração de processamento e worker |
| `wks-api` | `http`, `mcp`, `contracts`, `authentication`, `server` e `web/assets` |

`wks-api` depende de `wks-core`. O núcleo não importa o pacote da API, FastAPI, Uvicorn
ou MCP. A biblioteca pode ser instalada sem os transportes. O domínio permanece sem ORM
ou frameworks. Os adapters de processamento não precisam conhecer HTTP ou o frontend.

O workspace uv da raiz instala os dois pacotes. O executável `wks` pertence ao pacote API;
`wks worker` monta o núcleo pelo bootstrap do servidor. Migrações e operação Compose ficam
na raiz, pois coordenam o deployment e o schema compartilhado. O OpenAPI é gerado em
`wks-api/openapi.json`; os testes de integração ficam na raiz para verificar a fronteira.

Os assets são incluídos no wheel da API. Não há build Node na aplicação nem dependência
em execução do WOS. Node é usado apenas para os testes Playwright e a formatação do frontend.
