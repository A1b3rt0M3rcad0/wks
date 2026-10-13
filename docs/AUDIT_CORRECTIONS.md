# Diagnóstico e correções da auditoria de ingestão

Escopo: os cinco pontos genéricos do relatório recebido em 12/10/2026. A auditoria
relatou sucesso nos PDFs que testou; seu PDF privado não foi copiado nem publicado.
As qualificações deste repositório usam fixtures sintéticas e modelos locais
verificados. Regras de clientes externos não entram no WKS.

## P1 — Operação dos workers

`GET /v1/workers/status` e `wksctl worker status` informam heartbeat, filas,
contagens, último sucesso, último erro/retry, tentativa, parser e estados de cobertura.
Contagens e histórico de operações respeitam cliente, namespace/coleção delegados e
versões exatas; não incluem jobs de outro cliente. A lista técnica de processos é
limitada a 100 registros, sinalizando truncamento. A capacidade das filas é calculada
separadamente da página de processos.

As filas distinguem `worker_unavailable`, `capability_absent`, `stalled`,
`waiting_capacity`, `processing`, `queued`, `lease_recovery_wait`, `retry_wait`
e `idle`. Um backlog antigo
não é considerado parado se os processos estiverem ocupados. Contagens são do
escopo autorizado; ocupação dos processos é somente um sinal técnico agregado.
Um job com owner sem heartbeat válido não é apresentado como processamento ativo.
Seu lease ainda válido aparece como `lease_recovery_wait`, com contagem e próximo
vencimento no escopo autorizado; o fencing continua impedindo retomada antecipada.
O limiar de parada é `WKS_WORKER_STALL_SECONDS` (300 por padrão).

O heartbeat do processo é renovado durante a extração longa. O ciclo de manutenção
do lease agora tem teto de 15 segundos e a divulgação do processo ocorre a cada
30 segundos, abaixo do TTL de 90 segundos. Cada container possui um identity file
privado; `wks-worker-native --healthcheck` verifica o heartbeat daquele processo,
sem aceitar um peer saudável como substituto. Mídia e Docling têm probes próprios.
O probe verifica liveness: um worker que publica cobertura pending com ASR
explicitamente desabilitado pode estar vivo sem ter a capability de transcrição.

## P1 — Capabilities e cobertura multimodal

`/v1/capabilities` separa política da API, prontidão do worker e alcance da modalidade.
WAV/vídeo não aparecem como extraíveis apenas porque outro processo habilitou a flag.
PDF/Office anunciam a fila efetivamente selecionada pela política Docling/native.
Sem suporte ao sandbox necessário, um decoder não é anunciado como pronto.

ASR é transcrição probabilística de fala. Não interpreta eventos não verbais; essa
cobertura é `not_interpreted`. Silêncio pode produzir `no_speech_detected` e metadata
sem texto, sem ser anunciado como semântica completa. Frames de vídeo são amostrados,
não interpretação integral. OCR reconhece texto, não significado visual. Descrições
visuais geradas por provider registram pending/partial/described e mantêm
`semantic_completeness=not_measured`; zero imagens não é sucesso semântico.

`compose.multimodal.yaml` habilita opcionalmente ASR, vídeo e Docling, com os mesmos
paths absolutos e flags na API/workers. Os pesos devem ser baixados/verificados antes
do startup. O provider HTTP de visão continua opcional; nenhum provider externo ou
modelo de visão é apresentado como homologado. Veja [workers](WORKERS.md).

## P2 — CLI Windows

Os testes de permissões/atomicidade são separados dos testes de symlink. O gate
Windows exige uma pré-verificação real de symlink de arquivo e diretório; nenhum
teste de confinamento é ignorado. A falta de Developer Mode ou de
`SeCreateSymbolicLinkPrivilege` produz uma mensagem de pré-requisito ambiental.

O teste de manifest passou a verificar que o symlink foi criado: antes poderia
passar por arquivo inexistente quando a criação falhava. O loader de context agora
rejeita também um diretório imediato que seja symlink, assim como o writer já fazia.
A CLI em produção não precisa de privilégio para criar symlinks: esse privilégio é
necessário para o teste construir o cenário hostil. Em Windows, execute os testes
com Developer Mode habilitado ou com esse privilégio concedido ao usuário/processo.

## P2 — Consumidores, falhas e concorrência

Upload HTTP de mais de 8 MiB, ACK perdido após persistir a primeira parte, retomada
pelo offset confirmado, commit repetido/receipt e SHA-256 integral do original são
qualificados. O SDK Python classifica ACK incompleto ou incoerente como resultado
de escrita desconhecido; não repete a mutação automaticamente nem avança o progresso
sem confirmação. O consumidor consulta o estado e retoma.

O teste de timeout/retry usa leases reais no PostgreSQL, rejeita a publicação de
uma tentativa antiga e lê uma representação paginada enquanto a fonte é reprocessada.
Quatro consumidores mantêm o mesmo source_version/representation, ordinais, blocos,
coverage e cursor até o fim. O gate existente também transfere 1 GiB com retomada
na metade; essa transferência maior é executada no runner de CI com espaço adequado.

## P2 — Qualificação lexical

O corpus com qrels declara a língua dos documentos, testa stemming, acentos,
frase inglesa, casos de domínios diferentes, sinônimos fornecidos explicitamente
com `OR` e isolamento entre clientes. Inclui 208 documentos autorizados e um documento
privado de outro cliente. Extração nesse corpus é sintética/canônica, isolando a
avaliação da recuperação; os decoders reais possuem sua própria matriz de formatos.

Sem declaração de língua, a inferência curta pode resultar em `simple`, sem stemming.
Informe `metadata.declared_language` (`pt`, `en`, `simple`) na criação para qualificar
morfologia nessa língua. Alterar a declaração depois exige reprocessar a fonte.
Sinônimos não são expandidos automaticamente: `carro` não promete localizar
`automóvel`. Use `carro OR automóvel` para uma expansão lexical explícita.

O benchmark separado usa 100.000 segmentos sintéticos, dois clientes e 20 consultas,
com aquecimento. Mede custo de busca/indexação, não reconhecimento dos documentos,
qualidade geral ou capacidade/SLO de produção. Não há busca vetorial, embeddings,
qualificação semântica ou novos formatos presumidos por esse teste.

## Evidências executadas

- Lint/format do workspace, suíte Go CLI com race em Linux, probe do worker
  nativo e `worker status` da CLI contra a API real passaram.
- A suíte ampla local executou 223 testes: 219 passaram inicialmente e quatro
  falharam. Duas falhas exigiam apontar o backup para o container PostgreSQL local;
  duas expuseram validação de ACK insuficiente e foram corrigidas no SDK.
  A reexecução de 22 testes relevantes passou integralmente, sem skips. A revisão
  final de capacidade e recuperação do lease passou nos nove testes de diagnóstico
  de workers.
- ASR real, frames de vídeo, Docling local e a matriz de formatos foram executados.
  O provider externo de visão não foi homologado; o teste de conexão/coverage usa
  uma resposta controlada e é identificado como tal.
- Os [qrels e métricas de busca](evidence/audit-2026-10-12/search-quality.json)
  tiveram recall@10 e nDCG@10 médios de 1,0 nas oito consultas fornecidas.
  O [benchmark de 100 mil segmentos](evidence/audit-2026-10-12/benchmark-100k.json)
  mediu p95 de 1622,26 ms neste ambiente, sem certificação de SLO.
- A transferência de 1 GiB, dois testes de empacotamento completo e Windows/macOS
  são gates da CI; não foram desabilitados por limite de disco do ambiente local.

O [registro de verificação](evidence/audit-2026-10-12/local-verification.json)
conserva separadamente resultados locais e gates da CI/publicação.


A [CI completa da revisão final](https://github.com/A1b3rt0M3rcad0/wks/actions/runs/38328279177)
passou com **228 testes Python e cinco testes de browser, zero falhas e zero skips**.
Inclui upload/retomada de 1 GiB, regressões de empacotamento, ASR/Docling reais e
CLI/SDK Go com race em Linux, macOS e Windows. A pré-verificação Windows de
symlink de arquivo/diretório também passou. Consulte o
[registro da CI](evidence/audit-2026-10-12/ci-verification.json).


## Publicação concluída

A [release v1.2.0-rc.1](https://github.com/A1b3rt0M3rcad0/wks/releases/tag/v1.2.0-rc.1)
aponta para `cf3e8f6cf7ad8d2286849a89a0f674b33f572006`. A
[pipeline completa de release](https://github.com/A1b3rt0M3rcad0/wks/actions/runs/38328279482)
passou: candidato instalado/verificado, quatro imagens amd64/arm64, decoders reais
e smoke de API com workers independentes. Os 34 assets e as referências imutáveis
estão registrados na [verificação da publicação](evidence/audit-2026-10-12/publication-verification.json).

A [publicação npm](https://github.com/A1b3rt0M3rcad0/wks/actions/runs/38329911605)
de `wksctl@1.2.0-rc.1` passou usando OIDC/provenance. A instalação do pacote exato
e sua Skill foram qualificadas em Windows, macOS e Linux. Uma instalação nova do
registry público também confirmou versão/commit, checksums/launcher, `worker status`
contra a API real e a Skill com as orientações de diagnóstico.
Veja a [verificação da instalação](evidence/audit-2026-10-12/npm-install-verification.json).

Esta publicação é candidata, no canal `next`; `latest` permanece em `1.1.0`.
Para instalar exatamente as correções:

```sh
npm install -g wksctl@1.2.0-rc.1
wksctl worker status
wksctl init --agents
```

O relatório original não demonstrou travamento documental. Os cinco pontos foram
tratados como melhorias genéricas de operação, configuração e qualificação do WKS.
O PDF privado do relatório e seus identificadores não integram a publicação.
