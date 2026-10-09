# Busca textual

Somente PostgreSQL FTS + GIN. Texto e título recebem pesos lexicais; `websearch_to_tsquery`
aceita consulta natural e `phraseto_tsquery` aceita `mode=phrase`. Português/inglês usam
`unaccent` com stemming. Stopwords sem tsquery retornam `query_not_indexable`, sem varredura
irrestrita. `rank` é relativo à consulta; não representa verdade nem confiança semântica.

Filtros: namespace, collection, fonte, revisão, MIME, período, processor, availability e
idioma. Autorização precede paginação e aplica-se novamente em cada página. Grants podem
pesquisar versões antigas mesmo após novo upload; não revelam versões fora da seleção.

Cursor é HMAC, expira, fixa principal/grant, query, filtros, tamanho de página e watermark.
Keyset ordena rank decrescente e ID crescente; score é promovido a float8 antes de serializar
para manter a igualdade na próxima página. Publicações posteriores não entram naquele
cursor; revogações continuam prevalecendo. `current_published` seleciona a publicação mais
recente no watermark; `historical` permite representações/revisões anteriores autorizadas.

Resultados trazem source/version/representation/segment, snippet plain text, origem, locator
e assets. Read/outline têm limites separados. Blocos canônicos excepcionalmente grandes
recebem preview com `preview_only`, nunca truncamento silencioso. Reindexação conserva IDs
e usa o texto persistido, sem nova extração. Gerações de publicação pertencem ao namespace.

O benchmark em `evidence/benchmark.json` é carga sintética com duas identidades. A consulta
medida é deliberadamente ampla; o planner pode escolher scan quando todos os segmentos
correspondem. Não há SLO de produção fixado sem medir corpus/hardware do deployment.
