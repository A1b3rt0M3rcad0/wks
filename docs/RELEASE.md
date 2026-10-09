# Entrega WKS — verificação inicial

A atualização da organização e do frontend conforme o WOS está registrada em
[verificação de 2026-10-08](verification-2026-10-08-wos-alignment.md), com 76 testes
e quatro jornadas de navegador. Este documento preserva os resultados e commits
da entrega inicial; o schema atual e o frontend estão no registro mais recente.

Implementação independente do WKS, no escopo confirmado pelo usuário. Nenhuma alteração
foi feita nos repositórios Lipo/Woobe. O documento original está em `IMPLEMENTATION_PLAN.md`;
a aplicação dos critérios ao escopo WKS está em `ACCEPTANCE.md`.

Código de aplicação validado: `45b1995ee5aa9477b03606f185336bfe7aeec50e`.
A correção de empacotamento para o usuário não root está em
`a545e968b88ec9f89638d5aa0736bc574afeebe7`. Commits subsequentes de documentação/evidência
não alteram o código de aplicação. Consulte o HEAD do pacote para a entrega completa.

## Verificações concluídas

- **69 testes passaram; zero falhas, erros ou skips**, em 207,62 segundos.
- Ruff: verificação de código e formatação passaram.
- PostgreSQL 17 real: migrações, FTS/unaccent, autorização, histórico, publicação atômica,
  cursores, concorrência, fencing, timeout, revogação e expurgo interrompido/recovery.
- MCP streamable HTTP com cliente oficial contra servidor Uvicorn real, incluindo
  paridade com HTTP, acesso negativo, revogação, resource links e MIME dos recursos.
- PDF nativo/OCR e Docling com modelos locais fixados, incluindo Docling no subprocesso
  limitado do worker; DOCX/PPTX, imagem, assets e originais.
- ASR CPU real com faster-whisper, PyAV 15 e modelo tiny fixado; vídeo real com FFmpeg,
  transcrição temporal, frames recuperáveis e original preservado.
- Backup PostgreSQL + objetos, restauração em outra base, busca/refs/ACL preservados,
  reindexação sem mudar IDs e recusa de bytes corrompidos.
- Contrato S3 com Moto; proteção SSRF/DNS/redirect com testes controlados.

Resultados por caso e versões de dependências: [`evidence/validation.json`](evidence/validation.json).
Fixtures e seus hashes: `tests/fixtures/manifest.json`. Os pesos locais são verificados por
manifestos, não incluídos no Git nem no pacote de código.

## Execução Compose verificada

Imagem `bcfcfdc4be67a49f76a0c4c977de68ea87e84a16b944a687cacc49d71894a269`,
executada como usuário não root. Projeto `wks-delivery`: PostgreSQL e API saudáveis,
worker em execução, migrações concluídas com exit 0 e schema `0004`.

Endpoint local: **http://127.0.0.1:8081**. PostgreSQL do Compose usa porta local 55433;
a base separada dos testes usa 55432. Upload por streaming, checksum, processamento pelo
worker, busca, leitura, download do original e expurgo passaram no serviço em execução.
O cliente MCP oficial também inicializou, listou as seis Tools e consultou busca lexical.
O hash dos arquivos da aplicação/contratos/migrações dentro da imagem coincide com o checkout.

Registro: [`evidence/runtime.json`](evidence/runtime.json). O token do smoke fica somente
em `.local/delivery-token` (0600); não está nos relatórios, Git ou pacote. A instância é de
desenvolvimento, sem publicação em produção.

## Desempenho medido

[`evidence/benchmark.json`](evidence/benchmark.json) registra 100 mil segmentos sintéticos,
dois tenants e 50 buscas, após cinco warmups: mediana **840,56 ms**, p95 **1.816,01 ms**.
Ingestão de 20 textos UTF-8 curtos, da criação à publicação pelo worker: mediana **991,85 ms**,
p95 **1.821,60 ms**. A medição é do Service compartilhado; não inclui latência de upload HTTP.

O host oferece cinco CPUs lógicas e PostgreSQL 17.11. Testes Docling e um build Docker
rodaram simultaneamente. A consulta ampla corresponde a todo o corpus e permite scan
sequencial pelo planner. Estes valores qualificam esse ensaio, sem estabelecer SLO de produção
ou medir extração de 100 mil documentos. O script usa uma base descartável própria.

## Operação e limites de homologação

Use `README.md` para iniciar e `OPERATIONS.md` para modelos, proxy/CA, recovery e manutenção.
Perfis native/OCR funcionam por padrão; ASR, vídeo, captura e enrichment são explícitos.
O perfil Docling/ASR foi qualificado na instalação Python local. A imagem Compose padrão
instala o perfil native; extras de mídia exigem build e montagem dos modelos documentados.

S3 em cloud, captura com egress público, visão por provider externo, consumo multimodal por
um modelo, CI remoto e implantação em produção exigem a homologação do deployment respectivo.
Não foram declarados como resultados desta execução. Na rede cloud que exige proxy, o adapter
de captura por socket direto deve continuar desabilitado, ou receber um gateway com DNS
pinning seguro. O fallback MCP declara mídia disponível sem afirmar que chegou ao modelo.

## Preservação da entrega

Repositório de entrega: [A1b3rt0M3rcad0/wks](https://github.com/A1b3rt0M3rcad0/wks),
branch `master`, com o histórico completo da implementação. O pacote de código contém
os arquivos versionados, a documentação e os fixtures; não contém tokens,
.env, caches, pesos de modelos ou dados de usuários. O Git bundle preserva também os commits.

O rascunho cloud guarda instalação/inicialização e a revisão da entrega. Salvar o rascunho
não publica o ambiente; revise/salve/publique as configurações para reutilizar a instalação.
O código está preservado também no GitHub; os modelos e dados locais continuam fora do Git.
Não foi declarada restauração em uma nova sessão cloud.
