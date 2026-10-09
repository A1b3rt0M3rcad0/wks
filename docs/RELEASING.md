# Releases WKS

O fluxo segue o padrão consultado somente para leitura em `A1b3rt0M3rcad0/woobe-cli`,
commit `526d894341449a88b5dbcf0bccf066aa25cb900f`. Só o WKS é modificado.

## Versão e origem

`VERSION` e os três manifests Python definem o piso revisado, inicialmente `0.2.0`.
O workflow calcula a próxima versão usando os commits desde a maior tag reservada:
`feat:` incrementa minor; `fix:` e outros commits incrementam patch;
`!` ou `BREAKING CHANGE:` incrementam major, ou minor antes de 1.0.
Prereleases aceitam `X.Y.Z-alpha.N`, `beta.N` e `rc.N`. Wheels usam a forma PEP 440;
tags, CLI, OpenAPI e imagens conservam SemVer.

O publisher não reescreve os manifests nem cria commits de versão. O empacotador aplica
somente metadados da versão aos arquivos do commit selecionado, preservando os pins e
hashes das dependências. `wks version` e `/health/live` informam versão e commit.

Push em `master` calcula a versão; push em uma tag `vX.Y.Z` qualifica exatamente essa tag.
Disparo manual aceita versão e SHA completo opcional, integrado em `master`.
Uma tag explícita pode qualificar um commit fora de master. Releases são serializadas;
um evento atrasado de master é ignorado se uma origem mais recente já foi reservada.

## Gates e publicação

1. A identidade seleciona uma versão e um commit imutáveis.
2. O CI reutilizável testa esse commit em PostgreSQL, processamento e navegador.
3. São gerados os dois wheels, sdists, fontes completas, dependências nativas com hashes,
   OpenAPI, `release-manifest.json` e `SHA256SUMS`. A instalação isolada verifica o contrato.
4. A imagem GHCR é construída desses wheels para Linux amd64/arm64. O workflow verifica
   a identidade nas duas plataformas e qualifica HTTP, worker e primeira conta em Compose.
5. Só então publica a tag, os arquivos no GitHub Release e a imagem de versão. `latest`
   acompanha somente a maior versão estável publicada, sem regredir em um rerun antigo.

Uma versão existente nunca muda de commit, arquivos ou digest da imagem. Reruns reutilizam
a identidade e imagem existentes, conferem todos os hashes e completam somente uploads
faltantes de um draft. Arquivos conflitantes ou release publicado incompleto interrompem
o processo; não são sobrescritos. Para retomar, dispare o workflow com a mesma versão e SHA.
O candidato temporário tem tag `candidate-VERSION-SHA`; não é a imagem estável.

Não é preciso cadastrar um PAT: os jobs usam `GITHUB_TOKEN`, com `contents: write` e
`packages: write` somente na publicação. GitHub Actions e acesso ao GHCR devem estar
disponíveis no repositório. Falhas desses serviços impedem a publicação, mesmo com CI local aprovado.

## Instalar uma release

Baixe os assets de uma release e confira `sha256sum -c SHA256SUMS` antes de instalar.
Extraia o pacote de fontes para obter Compose, migrações, configuração e documentação.

```sh
WKS_IMAGE=ghcr.io/a1b3rt0m3rcad0/wks:0.2.0 docker compose up -d --no-build
```

Para instalação Python, use Python 3.12, PostgreSQL 17, FFmpeg e Tesseract com os idiomas
necessários, em um ambiente virtual:

```sh
uv pip install --require-hashes -r native-requirements.txt
uv pip install --no-deps wks_core-0.2.0-py3-none-any.whl wks_api-0.2.0-py3-none-any.whl
alembic upgrade head
wks api
# Em outro processo:
wks worker
```

A imagem publicada inclui o perfil native. Docling/ASR exigem os extras, modelos verificados
e configuração descritos em OPERATIONS.md; não estão embutidos nessa imagem.
Abra `/app/` e crie sua primeira conta; nenhuma credencial humana acompanha a release.
Em produção, configure HTTPS, `WKS_ENV=production` e `WKS_WEB_PUBLIC_ORIGIN`.

## Upgrade para 0.2.0

Faça backup antes de aplicar a migração 0006. Ela adiciona contas e limites de tentativas,
revoga cookies antigos e preserva os clients/tokens de integração e todas as fontes.
Instalações anteriores sem conta mostram o cadastro inicial. A nova conta começa com
seus próprios contextos; o WKS não presume que um client de integração pertença a ela.
Backup/restore conserva os hashes da conta e do token, mas limpa sessões e limites temporários.
Ao restaurar um snapshot antigo, confira o histórico de revogações: mudanças de senha,
token ou ACL posteriores ao backup também precisam ser reaplicadas.
