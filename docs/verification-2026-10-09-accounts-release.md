# Contas e releases — verificação de 2026-10-09

Implementação qualificada: `c1fbd5965f93fbb266199579e3854ea925dd8436`, sobre a entrega WOS
anterior. A revisão seguinte acrescenta evidências e amplia o smoke de release;
os 41 arquivos de código, assets e migrações mantêm os mesmos hashes do código qualificado.
Esta atualização modifica somente WKS. WOS e woobe-cli foram referências de leitura.

## Autenticação e navegador

- Suíte completa: **89 passed**, zero falhas/erros/skips, em **362,42 s**. Inclui
  processamento Docling/ASR com modelos locais verificados e 13 casos da nova autenticação.
- Verificação adicional: **16 passed**, zero falhas/erros/skips, em **38,53 s**:
  12 testes de identidade/versionamento e transporte com commits e tags Git reais e quatro testes de
  manutenção, incluindo restore com conta/senha/token e limpeza de sessões/limites.
- São **101 casos distintos** de backend/release; os quatro de manutenção foram reexecutados.
- Chromium: **5 jornadas passaram** em **55,38 s**, zero falhas/skips/retries. As quatro
  jornadas do workspace usam username/senha; a quinta parte de um banco sem conta e
  percorre cadastro, download do token, login incorreto/correto, recuperação sem username,
  revogação de cookie, rejeição do token consumido e rotação autenticada.

Casos de concorrência executam duas instâncias da API: somente uma criação da primeira
conta e um consumo simultâneo do token são aceitos. Limites são compartilhados no banco.
Credenciais fracas/longas, origem externa, CSRF inválido e client desabilitado são rejeitados.
Cookies de produção são Secure. Os relatórios e screenshots não contêm credenciais.

As capturas atuais estão em [ui-workspace](ui-workspace/README.md). Tokens ficam apenas na
resposta/DOM temporário e no arquivo baixado explicitamente pelo usuário; não no storage
do navegador. Senhas usam scrypt e somente o hash do token é persistido.

## Empacotamento e gates

Ruff passou nos 63 arquivos Python; Prettier e `git diff --check` passaram.
Os workflows passaram em actionlint **1.7.12**, obtido do release oficial com checksum verificado.
O OpenAPI gerado é idêntico ao contrato executável da API.

Dois empacotamentos independentes do commit qualificado geraram **SHA256SUMS idênticos**,
incluindo wheels, sdists, pacote de fontes, dependências nativas, contrato e manifesto.
Manifesto desse candidato de qualificação:
`c2068f463963fa11402cfdd0f9b0c8e44338ce22be092dac819f9a8376ead001`.
O candidato final é empacotado do commit final, com a identidade desse commit.
O primeiro envio remoto detectou que `uv build` gera um `.gitignore` na saída, omitido
pelo upload de Actions. A conferência bloqueou a publicação antes de criar qualquer tag.
O empacotador agora remove esse arquivo antes do manifesto; um teste constrói os pacotes
reais, simula o ZIP de Actions sem arquivos ocultos e verifica o candidato baixado completo.

Os wheels foram instalados em um ambiente virtual independente, sem importação editable.
Foram conferidos o caminho do pacote, versão/commit e **todo** o OpenAPI. Os 34 arquivos
de código/assets distribuídos coincidem byte a byte com o código testado.
Modificar um wheel ou informar outra revisão causou rejeição do candidato.
Também foi construído/verificado um candidato `0.3.0-rc.1`, com metadados Python PEP 440.
Overlays `0.2.1` e `0.3.0-rc.1` passaram em `uv lock --check --offline` com cache vazio,
preservando os 160 pacotes e hashes do lockfile.

## Imagem e operação

A imagem de qualificação foi construída por `Dockerfile.release` a partir dos wheels
verificados, mantendo TLS e CA do ambiente. Identidade local amd64:
`sha256:7612e003873bffde1eafb61616c06278a5977b37893212a789b72327522631aa`.
Executa como usuário `wks`, UID 10001, com versão `0.2.0` e commit de qualificação.

O smoke em Compose usa projeto, banco e volumes próprios descartáveis. Verifica migração
0006, readiness, contrato de versão/commit, assets, primeira conta, login com senha,
recuperação de uso único, revogação da sessão anterior, rejeição da senha antiga e login
com a nova. Em seguida, usa uma integração independente para upload streaming, SHA,
processamento no worker, FTS, leitura, original idêntico e expurgo.

A imagem final local foi reconstruída com o commit `11cb1628eeeb022e3ee6920572162e379493dc41`,
ID `sha256:3ba6831397de2d1ba3c082fbd03e19cc91846c5140e049aa5bdc0d6b5c389a29`.
A instalação de desenvolvimento foi migrada para 0006, ficou saudável e passou novamente
no smoke HTTP/worker. Preserva os volumes existentes e começa sem conta humana.
A correção posterior afeta somente empacotamento/testes/evidências; mantém o runtime qualificado.
O cadastro acontece em `/app/`. Clients de integração continuam separados.
Migrações anteriores não foram alteradas; 0006 adiciona as tabelas de autenticação e
invalida cookies anteriores sem apagar fontes, versões ou credenciais de integração.

## Publicação e limites da evidência

No commit `03da0d241da72ee582d160866ec63559ff95fa1b`, o CI remoto registrou **101 testes**,
zero falhas/erros/skips, em **80,46 s**, e cinco jornadas Chromium em **26,76 s**.
O candidato baixado de Actions teve checksums idênticos ao candidato local.
A build multiarch concluiu, mas o Docker clássico do runner recusou carregar amd64 e
arm64 pelo mesmo digest do índice. O teste agora seleciona o digest filho imutável de
cada plataforma no índice validado, preservando o digest do índice na publicação.

O [workflow](../.github/workflows/release.yaml) qualifica novamente o commit exato recebido
por GitHub Actions antes de publicar. O candidato final e seus checksums são verificados
independentemente. A execução local qualifica **amd64**; a verificação **amd64/arm64** e
a publicação GHCR pertencem aos jobs remotos e não são contadas como testes locais.
Consulte [Actions](https://github.com/A1b3rt0M3rcad0/wks/actions) e
[releases](https://github.com/A1b3rt0M3rcad0/wks/releases) para o estado efetivo da publicação.

Os extras Docling/ASR foram testados no host. A imagem de release inclui native/OCR;
extras e modelos devem ser configurados separadamente. Não foi homologado provider
multimodal externo, S3 de produção ou deployment público.
Salvar o draft do ambiente cloud não publica o snapshot e não comprova restauração em
uma nova task. As instruções e o ref do repositório são atualizados para a entrega atual.
