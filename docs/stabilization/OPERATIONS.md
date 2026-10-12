# Operação da estabilização WKS

O escopo é apenas WKS. Homologação externa, AWS real, Lipo/Woobe e certificação
de capacidade/SLO foram retirados pelo proprietário. Os resultados funcionais
usam PostgreSQL real, filesystem, fixtures locais e Moto para o adapter S3.
Não são garantia de acurácia documental ou capacidade de produção.

## Instalação e upgrade de 0.3.0

Use Python 3.12, PostgreSQL 17, uv 0.12.19 e Go 1.27.2 para desenvolver o CLI.
O usuário do CLI instalado não precisa de Python. `uv sync --frozen` instala o
workspace; `--extra docling --extra asr` habilita os perfis opcionais. Os sete
pacotes Python e os dois módulos Go mantêm fronteiras independentes. O frontend
continua incluído no wheel API e segue a referência WOS.

Antes do upgrade, faça backup coordenado do banco, objetos e ledger externo.
Pare ingress e drene API/workers antigos: writers 0.3.0 não publicam blocos
normalizados e não podem continuar após a migração. Preserve volumes. Instale
as novas versões e execute `alembic upgrade head` até **0015**. A migração
preserva originais, versões, representações, grants legados e filas anteriores;
faz backfill de blocos e metadata, não reprocessa arquivos automaticamente.
Inicie API e os workers por perfil. Verifique `/health/ready`, versão, leitura,
busca, download com SHA-256 e uma nova publicação. Não execute downgrade
destrutivo. Rollback usa banco e objetos coordenados da versão anterior, com
replay das decisões de privacidade aplicáveis; não restaure apenas SQL.

Use as imagens publicadas da mesma tag para API, worker-native, worker-docling
e worker-media. Não misture tags. Compose dá 360 segundos para os workers
terminarem a tentativa atual ao receber SIGTERM. Em cloud preserve proxy e CA
da sessão e faça builds sequenciais quando usar Docker VFS.

## Conta, credenciais e delegações

Uma instalação vazia guia a criação da primeira conta por username/senha. A
conta recebe um token de recuperação uma vez. Guarde-o em local privado; a
recuperação exige esse token e uma nova senha, invalida sessões, credenciais e
grants anteriores e entrega um token substituto. A instalação não cria conta
humana automaticamente.

O operador humano pode criar clients e credenciais com expiração. Segredos
aparecem apenas na primeira emissão; receipts repetidos devolvem a identidade
sem o token. Para rotacionar, emita outra credencial, salve/teste o arquivo
privado e revogue a anterior. Não repita emissão com outra chave após uma
resposta ambígua: consulte primeiro o receipt e revogue a emissão inacessível.
Executor requer grant explícito. Grants são somente de versões exatas para
leitura, ou de namespace/collection com ações limitadas, duração até uma hora.
Não delegam administração de identidades. HTTP e MCP compartilham as decisões.

Destruição usa preflight de cinco minutos ligado ao caller, scope e revision.
Remover collection preserva Sources; namespace só pode ser removido vazio.
Expurgo revoga imediatamente o acesso e mantém intent de exclusão física até
que pins ou falhas de storage permitam concluir. Receipts ficam por 90 dias
(configurável), portanto automações devem reconciliar dentro dessa janela.

## Storage, upload e recuperação

Filesystem usa promoção imutável, SHA-256, fsync e identidade persistente do
store. Trocar root/bucket por variável não migra dados. `wks migrate-storage`
copia e verifica lotes antes de atualizar localização, devolve `next_cursor` e
pode ser retomado. Preserve o provider de origem até a conclusão. Importação
S3 exige operador, namespace autorizado e `WKS_S3_IMPORT_PREFIXES` registrado;
não é Tool administrativa MCP.

`offset-sha256-v1` usa upload proxied autenticado, partes de 64 KiB até 16 MiB
por padrão, offsets confirmados e SHA-256 por parte e do arquivo final.
`WKS_UPLOAD_MAX_BYTES` limita o total (64 MiB por padrão, configurável até 5 GiB).
`WKS_NAMESPACE_MAX_BYTES` limita a reserva por namespace (1 GiB por padrão).
O teste de 1 GiB usa limites explícitos e bytes sintéticos esparsos; o padrão de
64 MiB não é anunciado como aceitação de 1 GiB. Abort/expiração não publicam.
S3 passa a multipart em 100 MiB, com partes de 8 MiB por padrão. ETag não é hash
de integridade. Não há URLs públicas persistentes ou presigned download.

Downloads têm HEAD, Range, ETag, If-Range, If-None-Match e checksum. A cada 1 MiB
ou 250 ms o stream revalida autorização, o que ocorrer primeiro; configuração
pode chegar a 8 MiB/1 s. A revogação vale dentro desse limite, sem retirar bytes
já entregues. Uma resposta 304 também exige autorização atual.

`wks backup --directory PATH --pg-container NAME` exporta snapshot MVCC e fixa
blobs ativos com pins. O lock global termina antes do pg_dump/cópia. Manifesto
contém schema, versão/commit, política, watermark e SHA-256; objetos grandes
usam manifesto JSONL. `wks restore` exige banco vazio, schema compatível e o
ledger assinado vivo. Tráfego fica bloqueado até validar bytes e reaplicar
exclusões/revogações. O ledger fica fora do snapshot de banco; não o substitua
pelo ledger antigo. Preserve também sua chave de assinatura, com acesso privado.
Snapshots antigos devem ser restaurados pelo código de schema correspondente
e só então migrados. Configure WAL/PITR do PostgreSQL separadamente: WAL não
restaura objetos. Retenha backups e ledger conforme sua política operacional.
Uploads ainda pendentes não são originais publicados: restore expira suas
sessões e offsets temporários. Reinicie a transferência com uma nova intenção;
o servidor não confirma offsets cujas partes não entraram no snapshot.

`wks audit-integrity` verifica um lote de originais por SHA-256; a biblioteca
aceita cursor para completar a auditoria. Falhas conhecidas tornam readiness
503. `wks garbage-collect` varre lotes com checkpoint persistido, quarantena
órfãos por uma hora (configurável) e limpa uploads expirados. Bytes referenciados
e pins não são removidos. GC não é substituto de expurgo autorizado. Originais e
histórico publicado permanecem enquanto referenciados; não há prune implícito.

## Workers e limites

Native processa texto, HTML offline, XML seguro, PDF/OCR, Office, XLSX/ODS, EPUB
e imagens; media e Docling são pacotes/filas separados. ASR/video são opt-in com
modelos locais e manifestos SHA-256. `/v1/capabilities` informa formatos aceitos,
worker vivo, limites e cobertura; aceitar bytes não implica extrair conteúdo.
Confiança/acurácia não medida permanece desconhecida. Reprocessar preserva o
original e cria outra representação. `/v1/namespaces/{id}/reprocess` faz plano
por padrão e agenda até 25 Sources por página quando `dry_run=false`, com
filtros de versão de processor, incompletude e ausência de índice.

Policies de namespace limitam bytes, input, páginas, perfis e concorrência;
metadata tem schema offline limitado e filtros indexados explícitos. O limite
inicial é dois jobs por namespace/perfil; filas usam fairness, leases e retries
com jitter. Rate limit compartilhado pelo PostgreSQL é 600 ações/minuto por
contexto por padrão; idempotency replay não consome outra mutação.

Decoders têm orçamento de memória/CPU/tempo, ambiente sem credenciais do broker
e rede bloqueada por seccomp. `WKS_DECODER_SANDBOX=strict` exige também Landlock
e falha fechado se indisponível. Este kernel cloud não oferece Landlock; aqui
foi verificado bloqueio de rede e recusa do modo strict. Isolamento de filesystem
não é prometido neste host. Não coloque segredos no diretório legível do worker.

## Métricas e alertas

`/metrics` é restrito ao operador, com contadores HTTP, duração, bytes registrados,
jobs por fila/status, idade da fila e falhas de original. Nenhum label inclui
tenant, Source, query ou token. Worker `--metrics-port PORT` expõe contadores de
tentativas/duração apenas em loopback; use um scraper local por processo. O
endpoint API não agrega histogramas privados de processos worker diferentes.
O dashboard em `observability/dashboard.json` usa esses nomes públicos.

Alertas sugeridos: readiness indisponível, original ausente/corrompido, oldest
queued acima do orçamento do perfil e aumento de falhas. Defina os limites após
medir sua instalação. Não há SLO, RPO/RTO ou benchmark de um milhão certificados
nesta entrega; avaliação local de relevância usa qrels explícitos pelo SDK.

## Contratos e troubleshooting

Read/outline/block são paginados e dizem `complete`, `next_cursor` ou
`next_offset`; export persistido fornece o conteúdo integral e checksum. Source
inclui até as 100 versões mais recentes com `versions_complete`; use o endpoint
de versions para percorrer todo o histórico. History pagina representações e
operações com corte temporal. Search é lexical PostgreSQL FTS, pg_trgm fuzzy e
proximidade, com snippets e facets autorizados. Não usa vetores nem BM25 externo.
Nenhuma evidência local justificou introduzir outro motor.

Erro de transporte em escrita significa `unknown`, nunca sucesso. Guarde a
operation key e consulte `/v1/receipts/{key}`. `reconcile_required` demanda
restaurar a capacidade de storage/reconciliar o journal, preservando a mesma
chave. Um novo `Idempotency-Key` pode criar outra intenção. Não copie tokens,
conteúdo ou ambiente para logs de diagnóstico. Veja também SECURITY.md e
THIRD_PARTY_NOTICES.md antes de distribuir workers/modelos.
