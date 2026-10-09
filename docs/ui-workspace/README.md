# Workspace humano WKS

Acesse `/app/` na API. A credencial de um client provisionado abre uma sessão; executors
continuam usando MCP e grants. O seletor da sidebar separa os contextos desse client.

- **Resumo:** totais calculados no servidor e cobertura disponível das fontes.
- **Fontes:** catálogo paginado, filtro de disponibilidade, texto, upload e bookmarks.
- **Busca:** FTS lexical ou frase em português, inglês e texto simples; filtros por coleção.
- **Leitura:** blocos, origem, versões imutáveis, avisos de cobertura, assets e original.
- **Coleções:** criação e vínculo de fontes; consulta por coleção.

Processamento acontece no worker. Use Atualizar após enviar uma fonte; a interface informa
quando o texto ainda não está publicado. Uploads do navegador têm limite de 64 MiB ou do
limite configurado, o que for menor. Downloads e previews usam a mesma autorização da API.
ASR, vídeo e captura continuam dependentes das flags e modelos documentados.

Os screenshots são gerados pelos testes reais em Chromium sobre PostgreSQL e worker, com
fixtures sintéticas próprias do WKS. Não são imagens conceituais:

| Captura | Jornada |
| --- | --- |
| [Resumo](01-resumo.png) | Contagem integral de 26 fontes, incluindo cobertura |
| [Fontes](02-fontes.png) | Biblioteca paginada |
| [Busca](03-busca.png) | Recuperação lexical e origem |
| [Leitura](04-leitura.png) | Blocos e representação fixada |
| [Mídia](05-midia.png) | Asset preservado de PDF |
| [Mobile](06-mobile.png) | Layout de 390 px sem overflow horizontal |

Referência WOS: `A1b3rt0M3rcad0/wos`, commit
`0fb77fe400e10dba6fb519287a86fb45a5edcdb2`, acesso somente de leitura.

## Reproduzir as jornadas

Após instalar Python, dependências, PostgreSQL e os modelos dos demais testes:

```sh
npm ci --prefix tests/web
cd tests/web && npx playwright install --with-deps chromium && cd ../..
uv run --no-sync python scripts/browser_fixture.py
uv run --no-sync python scripts/browser_server.py
# Em outro terminal:
uv run --no-sync python scripts/browser_server.py --worker
# Em outro terminal:
npm --prefix tests/web test
```

O fixture cria somente a base dedicada `wks_browser_test`, provisiona a credencial em
`.local/browser-token` com permissão 0600 e gera 26 fontes. A limpeza se limita às fontes
criadas pelas jornadas nessa base. Nunca apontar essa base para dados reais.
Playwright usa Chromium instalado no sistema quando disponível; caso contrário usa o
browser instalado pelo comando acima. Traces e vídeos ficam desativados para não capturar
credenciais. A autenticação em testes preenche o campo sem registrar seu valor.

Produção exige HTTPS e `WKS_WEB_PUBLIC_ORIGIN=https://seu-host`, além de
`WKS_ENV=production`. O TTL padrão é 8 horas; `WKS_WEB_SESSION_TTL_SECONDS` aceita
300–86400 segundos. Sessões expiradas são removidas no próximo login; logout revoga o cookie
no banco e desabilitar o client invalida suas sessões imediatamente.
