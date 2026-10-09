# MCP remoto

SDK oficial `mcp`, versão exata no uv.lock, transporte streamable HTTP em `/mcp`, stateless.
Versão de protocolo é negociada com o cliente oficial nos testes; não se pressupõe suporte
a uma especificação futura nem equivalência automática com a versão instalada da Woobe.

Tools disponíveis: `list_sources`, `search`, `outline`, `read`, `read_asset`,
`get_processing_status`. Todas têm `readOnlyHint`; não há upload, captura, revoke ou delete
no catálogo de Tools. Parâmetros são consulta/refs/filtros/paginação; headers/grants ficam
fora do schema visível ao modelo. Mesmos casos de uso da API HTTP, sem SQL no transporte.

`read_asset` retorna metadata e `resource_link` com URI WKS. `resources/read` resolve imagens
pequenas com bytes e MIME real (limite 1 MiB); mídia maior usa gateway HTTP autenticado.
Links/metadata não provam que um modelo viu/ouviu mídia. Até qualificação de um consumidor
externo, `delivery_status=media_available_but_not_delivered_to_model`, inclusive se o caller
pedir `inline`. Não se anuncia visão nativa por devolver base64 ou URI como texto.

O client executor deve enviar bearer próprio e `X-WKS-Grant` específico de execução em
TODAS as requisições, inclusive resources/read. A API revogada recusa a próxima requisição;
não há estado de permissão congelado em sessão MCP. Host/Origin precisam estar na allowlist
do deployment (`WKS_MCP_ALLOWED_HOSTS`, `WKS_MCP_ALLOWED_ORIGINS`). Nunca desligue a proteção
de rebinding para expor um endpoint público.

Testes em `tests/test_mcp.py` fazem initialize/list/call/read-resource contra Uvicorn real,
comparam HTTP/MCP, verificam isolamento, MIME e fallback. A aprovação de Tools e entrega
ao provider dentro de outro runtime pertencem à homologação daquele consumidor.
