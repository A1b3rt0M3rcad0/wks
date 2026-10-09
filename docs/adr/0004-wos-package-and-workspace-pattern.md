# ADR 0004 — Organização e workspace humano conforme o WOS

Data: 2026-10-08. Estado: aceito.

O usuário pediu organização de código e frontend seguindo o WOS e autorizou leitura de
`A1b3rt0M3rcad0/wos`. Referência consultada:
`0fb77fe400e10dba6fb519287a86fb45a5edcdb2`. Nenhum arquivo desse repositório foi alterado.

Adotamos dois pacotes instaláveis em um workspace uv: `wks-core` e `wks-api`.
As responsabilidades correspondem às fronteiras do WOS sem trocar a linguagem do WKS.
HTTP e MCP continuam chamando os mesmos casos de uso. O domínio é puro; o Service conserva
seu mapeamento ORM pragmático existente. `ports` declara as fronteiras de adapters.
Migrações permanecem na raiz. A tabela de sessões pertence ao adapter de autenticação;
backup/restore administrativo conhece o schema compartilhado e invalida essas sessões
na restauração, evitando reativar cookies revogados depois de um backup.

O frontend é servido pela API em `/app/`, com JavaScript e CSS nativos incluídos no wheel.
A referência visual usa superfícies claras e quentes, sidebar contextual, abas, acento
laranja e leitura em painel lateral. Os componentes e textos foram escritos para os
fluxos de fontes, busca lexical, versões, coleções e mídia do WKS.

Credenciais client são trocadas por sessões opacas HttpOnly, SameSite Strict e Secure em
produção. O banco armazena apenas o hash da sessão. CSRF e origem protegem mutações por
cookie; Bearer e MCP conservam seus contratos. Não se armazena credencial ou conteúdo em
localStorage/sessionStorage. Dados de documentos são exibidos como texto; CSP restringe
scripts e recursos. O frontend não interpreta HTML enviado como conteúdo de fonte.

O catálogo e as métricas usam consultas autorizadas do Service, com paginação no servidor.
Respostas antigas são descartadas após troca de contexto ou consulta. Formulários preservam
a chave de idempotência para repetir uma solicitação cuja resposta foi interrompida.
