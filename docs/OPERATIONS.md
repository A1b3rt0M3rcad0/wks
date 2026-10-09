# Operação

## Instalação e serviços

Use checkout existente. Tasks cloud já são isoladas; não crie Git worktree para setup.
Instale com uv.lock frozen, aplique `alembic upgrade head`, provisione client operacional e
inicie API/worker separados. Nenhum serviço externo de agentes é requisito de readiness.
Para dois workers, `docker compose up -d --scale worker=2`. O limite Compose é 2 CPUs/4 GiB
por worker; ajuste após medir o corpus. O subprocesso tem memória, CPU e deadline próprios.

Compose usa PostgreSQL 17 e volume de objetos filesystem de desenvolvimento. S3-compatible
é selecionado por `WKS_STORAGE_PROVIDER=s3`, bucket/region/endpoint e cadeia de credentials
do SDK boto3. Não inclua AWS secrets no repo ou em logs. Operação de produção exige bucket
privado, encryption/lifecycle/backup apropriados e TLS no ingress. Não use a senha local do
Compose nem env `development` em produção; configure cursor secret forte e distribuição de
tokens pelo secret manager. Cloud credentials injetadas devem ser usadas pelo SDK normalmente.

## Docker em cloud com proxy

Preserve o Docker config e os defaults de proxy da sessão. Containers não herdam o trust
store do host; use BuildKit secret para o bundle fornecido, sem desabilitar verificação:

```sh
export BUILDX_CONFIG=/workspace/wks/.local/buildx
docker build --secret id=cloud_ca,src=/etc/ssl/certs/ca-certificates.crt \
  -f Dockerfile.api --target base -t wks-api:local .
docker compose up -d --no-build
```

`BUILDX_CONFIG` desloca somente os arquivos de trabalho do plugin; não substitui registry
credentials do Docker config. O secret existe só nos RUNs que instalam dependências. Para
Compose builds, use a configuração de secret equivalente. Runtime HTTP que precise do proxy
também exige CA read-only e `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` conforme o SDK. Não faça COPY
de certificados específicos de sessão na imagem nem substitua proxy por rota direta.

Environments com driver VFS podem copiar layers inteiros; preserve espaço antes de build e
evite rebuilds simultâneos. Cache de builds antigos pode ser removido sem apagar volumes de
PostgreSQL/objetos. Nunca use `compose down -v` em uma instalação com dados a preservar.

## Perfis e modelos

Defaults: native, OCR on, ASR/video/capture off, enrichment none. Instale extras opcionais
com `uv sync --frozen --extra docling --extra asr`; os wheels Torch Linux são CPU oficiais.
`scripts/download_models.py` fixa revisions e valida SHA-256 LFS/git blob. Do not utilizar
download parcial sem manifesto final. Artifacts/weights não são commits de código.

```sh
HF_HOME=/workspace/.cache/huggingface HF_HUB_DISABLE_XET=1 \
  .venv/bin/python scripts/download_models.py --destination .local/models/asr-tiny
HF_HOME=/workspace/.cache/huggingface HF_HUB_DISABLE_XET=1 \
  .venv/bin/python scripts/download_models.py --docling --destination .local/models/docling
```

O ambiente precisa permitir huggingface.co e o CDN efetivamente usado pelos pesos
(`us.aws.cdn.hf.co` na homologação). Não é necessário token para estes repositórios públicos.
Nunca peça/registre token só porque o Hub emitiu aviso de rate limit. Se o CDN mudar, acrescente
somente o hostname requerido à política e verifique o acesso antes de repetir o download.

Monte os modelos no worker para Compose, fixe paths no environment e inclua `WKS_BUILD_EXTRAS`.
Nunca habilite ASR sem modelo local nem anuncie precisão universal do modelo tiny. Docling
indisponível deixa warning e fallback native; não desaparece texto previamente publicado.
Captura em uma rede só com proxy necessita gateway de captura com DNS pinning seguro;
o adapter de socket direto fica restrito a deployments com egress público permitido.

## Recovery, retenção e expurgo

```sh
.venv/bin/wks reconcile
.venv/bin/wks garbage-collect
.venv/bin/wks reindex
```

Reconcile retoma deleções interrompidas e identifica órfãos; o worker executa a cada minuto.
Leases expirados são reclamados pelo próprio dispatcher com geração nova. GC bloqueia
promotions em curso, expira upload sessions e cursors e remove objetos não referenciados
somente sob prefixos reconhecidos do WKS. Não apaga chaves de outros consumidores do bucket.
Agende GC conforme volume. Reindex reconstrói FTS sem executar OCR/ASR e preserva referências.

Revogação bloqueia imediatamente todas as leituras. Delete bloqueia primeiro, apaga originais,
staging e derivados, retira segmentos/representações e só então confirma estado deleted.
Falha física fica pendente de reconciliação; não emite evento de expurgo concluído antes disso.
Audit/receipt/job IDs e hashes podem permanecer como tombstones, sem conteúdo ou filename.

## Backup e restauração

```sh
.venv/bin/wks backup --directory .local/backup-001 --pg-container wks-postgres
# Em instalação com pg_dump 17 local, omita --pg-container.
# Configure WKS_DATABASE_URL e WKS_STORAGE_PATH para destino vazio, separado:
.venv/bin/wks restore --directory .local/backup-001 --pg-container wks-postgres
.venv/bin/wks reindex
```

Backup adquire lock de manutenção que sincroniza DB e bytes com uploads/publicações/expurgo.
Inclui dump custom, manifesto e objetos verificados. Snapshot é recusado se faltar original
ativo. Restore exige DB vazio e valida dump/todos os hashes antes de carregar. Nunca faz
reset de uma base populada. Use pg_dump compatível com PostgreSQL 17. Credentials entram
por environment do subprocesso, nunca parâmetros de CLI/log.

Guarde backups com acesso restrito e encryption na infraestrutura. Prazo inicial recomendado
para homologação: 7 dias de backups, purge de exclusões no ativo imediatamente; produção deve
fixar prazo/consentimento e registrar como exclusões são retiradas também dos backups. Restore
de backup anterior à revogação exige replay do ledger de exclusões mais recente antes de abrir
tráfego. Não reative acesso a dados excluídos por simplesmente restaurar snapshot antigo.
Tokens de provisionamento e cursor secret ficam no vault/local configuration e são preservados
separadamente; hashes no dump não recuperam secrets.

Restore conserva os hashes das contas/senhas/tokens de recuperação e remove as sessões
de navegador e limites temporários do snapshot. Usuários devem fazer login novamente;
isso impede reativar cookies revogados entre o backup e a restauração. Reaplique também
trocas de senha e revogações de tokens posteriores ao snapshot antes de abrir tráfego.

## Frontend e sessões

O frontend e seus assets fazem parte do pacote `wks-api` e da imagem padrão, em `/app/`.
Uma instalação sem conta apresenta o cadastro inicial; depois mostra username/senha.
Não é necessário provisionar um client para entrar no navegador. Salve o token apresentado
no cadastro: a recuperação exige esse token e uma nova senha, revoga as sessões anteriores
e entrega outro token. Dentro da conta, pode gerar outro token confirmando a senha atual.
Clients HTTP/MCP continuam sendo provisionados separadamente por CLI.
Não há servidor Node em produção. Use HTTPS, `WKS_ENV=production`, cursor secret forte e
`WKS_WEB_PUBLIC_ORIGIN` com a origem pública exata (sem barra final). Cookies em produção
são Secure; não se deve tentar login de produção em HTTP. Configure o proxy reverso para
servir `/app/`, `/v1/` e `/mcp` no mesmo host. Não é necessário habilitar CORS.

`WKS_WEB_SESSION_TTL_SECONDS` define 300–86400 segundos (padrão 28800). Sessões expiradas
são removidas no próximo login. Logout revoga a sessão no banco; desabilitar o client
invalida todas as suas sessões. O MCP não usa essa autenticação de navegador.
Login, cadastro, recuperação e rotação usam limites compartilhados no PostgreSQL.
Configure proxies confiáveis para que o endereço do cliente seja determinado corretamente;
não confie em headers encaminhados por qualquer origem.

Veja [RELEASING.md](RELEASING.md) para tags, GHCR, instalação e migração 0006.

## Observabilidade e rollout

Readiness checa migration, DB, FTS e storage; não certifica um provider remoto. `/metrics`
é autenticado, com contagem/latência HTTP. Processing stages guardam duração/tentativa/estado;
jobs e coverage são observáveis por API/DB e outbox. Falhas de provider preservam consulta
ao texto já publicado. Não coloque texto, áudio, credenciais ou IDs de aluno em labels.

Rollout: validar migrations/restore em base isolada, habilitar leitores, qualificar o corpus,
então ativar flags individuais. Rollback de código requer schema compatível; downgrade
destrutivo é recusado. Não excluir originais/versões para voltar uma flag. Lipo/Woobe podem
adotar contratos posteriormente, com grants protegidos e citações verificadas pelo backend.
