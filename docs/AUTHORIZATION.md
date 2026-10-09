# Autorização e isolamento

Contas humanas são criadas em `/app/` com username (3–64 caracteres, normalizado para
minúsculas) e senha de 12–128 caracteres. O bootstrap admite somente a primeira conta,
com lock PostgreSQL entre réplicas. Clients de integração já existentes não contam como
contas humanas. A conta tem seu próprio client e continua sujeita ao isolamento de namespaces.
Não há conta padrão, cadastro público adicional ou associação automática a dados de outro client.

Senhas usam scrypt com salt aleatório; tokens de recuperação possuem 256 bits de entropia
e apenas SHA-256 é armazenado. Cadastro, recuperação e rotação autenticada mostram o token
uma única vez. Recuperação usa token e nova senha, consome o token, entrega outro e revoga
todas as sessões anteriores na mesma transação. A rotação exige senha atual e CSRF.
Tentativas têm limites compartilhados no PostgreSQL por ação/IP, com chaves hash e janela
de cinco minutos. Atrás de proxy, configure somente proxies confiáveis no servidor.

Cookies são HttpOnly/SameSite Strict, Secure em produção, com CSRF e validação de origem
nas mutações. Credenciais não vão para localStorage/sessionStorage nem logs. A migração
0006 revoga cookies anteriores; dados e credenciais de integração são preservados.

Provisionamento de integrações é operacional por `wks provision-client`. Tokens aleatórios são gravados
somente no arquivo indicado (`0600`); PostgreSQL conserva apenas SHA-256. Secret manager/TLS
e distribuição de credenciais pertencem ao deployment. Não há token mestre padrão na API.

Clients possuem somente os próprios namespaces. Executors exigem bearer próprio **e**
`X-WKS-Grant` para toda consulta. O issuer cria grants com caller binding, audience,
revisões exatas, ações `read/search/asset_read`, finalidade e TTL máximo de uma hora.
Cada operação revalida caller ativo, audience, expiração, revogação, versão e ação.
Um filtro só reduz o conjunto autorizado. UUID conhecido não contorna esse predicado.

HTTP e MCP recebem headers de transporte, não tokens no prompt ou argumentos das Tools.
Clientes externos devem injetar o grant por Run/Session em contexto protegido. WKS não
implementa essa capacidade dentro da Woobe e não certifica um runtime externo sem teste.

Recursos individuais invisíveis e inexistentes têm a mesma resposta 404. Source revogada
é removida imediatamente de busca/leitura/mídia; downloads em andamento revalidam ACL a
cada chunk. O gateway não emite links públicos temporários capazes de sobreviver à revogação.
Ref verificada sintaticamente não basta: `/v1/references/verify` consulta publicação/ACL.

Uploads não usam nomes do cliente como caminhos; originais só são ativados após tamanho
e SHA confirmados no objeto armazenado. MIME é detectado pelos bytes. ZIPs são inspecionados
para traversal, expansão e número de entradas. CPU/memória/páginas/duração/frames/bytes e
quota por namespace são limitados. HTML é baixado como attachment com `nosniff`.

Captura é desligada por padrão. Quando habilitada, só HTTP(S) público, portas padrão,
sem userinfo/cookies/auth encaminhada, com redirects limitados, validação de **todos** os IPs
resolvidos e conexão ao IP validado (SNI/TLS no hostname original). Rede privada, loopback,
metadata e rebinding são bloqueados. Proxies genéricos que refazem DNS não são aceitos nesse
adapter; em cloud sem egress direto, a captura exige um gateway que ofereça pinning seguro.

MCP mantém proteção contra DNS rebinding e exige allowlist de Hosts/Origins do deployment.
Logs não recebem bearer, headers, texto integral ou bytes. Erros de validação não ecoam input.
Sistemas consumidores precisam renderizar snippets como texto e separar dados de instruções.
