# Entrega dos workers em pacotes — 2026-10-09

Escopo: somente WKS. WOS e woobe-cli permanecem referências de leitura. A API/Core,
o runtime e os perfis nativo/OCR, Docling e mídia formam seis pacotes instaláveis.
A versão revisada é 0.3.0; veja [WORKERS.md](WORKERS.md) e [RELEASING.md](RELEASING.md).

## Evidências locais

| Verificação | Resultado |
| --- | --- |
| Suíte completa PostgreSQL, contas, contratos, processamento e release | 104 testes aprovados, sem skips |
| Release, incluindo novo caso de isolamento dos índices de pacotes | 13 aprovados |
| Filas, fronteiras, leases e novo job Docling em subprocesso limitado | 4 aprovados |
| Casos distintos nas execuções acima | 106; as reexecuções não são somadas novamente |
| Chromium com API e worker reais | 5 jornadas aprovadas, sem skips ou retries |
| Ruff, formatação, versões e sintaxe dos workflows | Aprovados |
| Wheels em ambientes separados | API sem workers/decoders; nativo sem API/Docling/mídia |
| Container nativo, sem rede, usuário não root | PDF: texto nativo, OCR e quatro assets |
| Container mídia, sem rede, usuário não root | ASR real em áudio e vídeo; dois frames e transcrição |
| Compose com API, nativo e mídia em imagens diferentes | Cadastro, login, recuperação, revogação de sessões, uploads de texto/áudio, publicação, original, FTS e expurgo |
| Migração 0006 → 0007 no ambiente preservado | Três jobs mantidos e roteados corretamente; backup anterior preservado |

A publicação Docling foi verificada pela fila e pelo subprocesso limitado, com modelos
locais fixados, OCR e FTS, sem usar o fallback nativo. A qualificação de mídia utilizou
faster-whisper local e FFmpeg; não demonstra interpretação de sons não verbais ou visão
por um provider externo. Nenhum consumidor Lipo/Woobe foi alterado ou homologado.

O smoke de contas usa um projeto e volumes descartáveis próprios. Não cria uma conta
humana na instalação entregue e não apaga seus volumes. Tokens e credenciais de fixture
ficam em arquivos privados ignorados, fora dos relatórios e dos artefatos de release.
Os PNGs da UI anterior foram preservados, pois esta atualização não muda o design.

A imagem local da API foi empacotada a partir de
`17fc26760f03717811cabb554758cb0cd24a23d0`. O código de runtime dos seis pacotes é o mesmo
na entrega; os commits posteriores ajustam builds, testes, CI e documentação. Essa
qualificação local cobre amd64. O setup executou os Dockerfiles de desenvolvimento e
usou o Dockerfile de release para verificar os wheels da API.

## Gates da publicação

O [workflow de release](../.github/workflows/release.yaml) qualifica o commit exato antes
de publicar. Gera os seis wheels/sdists, quatro arquivos de dependências com hashes,
OpenAPI, fontes completas e manifest/checksums. São vinte assets.

Cada imagem — API, nativo, Docling e mídia — é construída dos wheels para amd64/arm64.
A identidade é executada nas duas arquiteturas; os decoders são qualificados com fixtures
reais em amd64, sem rede. Docling é exercitado diretamente, sem fallback, e mídia usa
os modelos verificados. Um job adicional verifica o Compose com as imagens por digest.
Somente depois publica as tags imutáveis das quatro imagens e o GitHub Release.
Os resultados remotos ficam no [GitHub Actions](https://github.com/A1b3rt0M3rcad0/wks/actions)
e a distribuição em [v0.3.0](https://github.com/A1b3rt0M3rcad0/wks/releases/tag/v0.3.0).

O PyTorch exige seu índice CPU no export Docling. Os demais componentes usam somente
seus próprios requirements; todos os installs de release mantêm pins e hashes. A regressão
que detecta um índice CPU indevido na API/nativo/mídia executa o empacotador real.

Durante o build local, o driver VFS atingiu o limite de disco. Foram removidos caches de
build e imagens de teste sem uso; volumes de dados e o backup foram preservados. Evite
sobrepor builds e múltiplos stacks descartáveis nesse ambiente de 32 GB.
