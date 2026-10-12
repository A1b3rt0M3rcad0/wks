# wksctl

Baixe o arquivo de sua plataforma na release e valide `SHA256SUMS`. Extraia
`wksctl` (ou `wksctl.exe`), LICENSE e NOTICE. O binário é autônomo; API e
PostgreSQL ficam no servidor. `help --json`, `version` e `schema` são offline.

Configure endpoint e autentique fornecendo a credencial pela entrada padrão,
sem token em argumento. O comando exige `--token-stdin`; contextos ficam em
arquivo privado 0600 ou ACL exclusiva do usuário no Windows. `context list`
redige credenciais. Use `context set --namespace ID` para o contexto de trabalho.
`doctor` verifica readiness e capabilities reais.

Comandos HTTP recebem `--id`, `--json-body FILE` e `--key OPERATION_KEY`.
Metadata exige `--if-match REVISION`. `--all` em leitura percorre páginas como
NDJSON, com proteção contra ciclos. Exclusões exigem `--yes`, preflight e
confirmação vinculada; `--dry-run` não envia escrita. `reconcile --key KEY` (ou `operation receipt --id KEY`) reconcilia uma escrita incerta; `operation wait` espera estado confirmado.
Exit codes: 0 sucesso; 1 erro API/assertion; 2 entrada local inválida; 3 escrita
ambígua/reconciliação; 4 autenticação/autorização. JSON fica em stdout; erros em
stderr. Não capture stdin de credenciais em logs.

`source upload --file PATH --title TITLE` e `upload resume --id UPLOAD --file
PATH` usam offsets confirmados e digests. `export create --id REPRESENTATION
--format markdown-bundle`, `export get` e `export download --output FILE`
produzem conteúdo integral com verificação SHA-256. Asset download também
verifica o checksum. O arquivo de destino não é promovido se a verificação falhar.

`init --directory PROJECT --agents` cria `.wks/manifest.json` e a Skill WKS
local, preservando AGENTS.md existente. Manifesto JSON/YAML declara namespace,
Sources por `external_ref`, conteúdo/arquivo, metadata e collections. Arquivos
devem ficar dentro do projeto, inclusive ao resolver symlinks. Credenciais não
são campos válidos de manifesto. `plan --manifest FILE` mostra alterações;
`apply --yes` / `sync --yes` aplicam com checkpoint recuperável. Alteração de
conteúdo cria revisão, metadata usa If-Match e reaplicação não duplica Sources.
Remover declaração do manifesto não remove dados remotamente: prune é explícito
pelos comandos de lifecycle, após revisar o impacto.

`test --tests .wks/tests.yaml` lê um conjunto explícito de fixtures e assertions,
cria um namespace isolado, ingere por HTTP e espera o worker. Exemplo:

```yaml
schema_version: 1
fixtures:
  - name: physics
    title: Physics
    text: Electrical resistance in a parallel circuit.
queries:
  - name: lexical
    query: resistance
    language: simple
    expected_sources: [physics]
```

O resultado contém assertions passadas/falhas, não uma nota de acurácia universal.
Não apaga automaticamente o namespace de teste. O endpoint de reprocessamento
usa `dry_run=true` por padrão e permite plano filtrado antes de agendar.

Desenvolvimento: Go 1.27.2, `go test -race ./...` em cada módulo. CI executa os
contratos nativamente em Linux, macOS e Windows; a suíte Python também usa o CLI
e ambos SDKs contra a API real. Releases levam seis binários de OS/arquitetura,
sete wheels Python, fonte, OpenAPI, SBOM e hashes.

## npm e instalação da Skill

```sh
npm install -g wksctl
wksctl version
wksctl skill install --directory ./meu-projeto
# Sem instalação global:
npx --yes wksctl skill install --directory ./meu-projeto
```

O pacote npm contém os seis binários Go (Linux/macOS/Windows, x64/ARM64),
licenças e a Skill. Node 20+ executa o binário correspondente; Python e Go não
são dependências de execução. Não há scripts de instalação nem downloads adicionais.
A Skill fica em `.agents/skills/wks/SKILL.md`; a instalação independente não
cria manifest, conta ou contexto, preserva `AGENTS.md` e recusa sobrescrever uma
Skill personalizada. Repetir a instalação da mesma Skill não altera arquivos.

`.github/workflows/npm.yaml` testa o cliente/SDK, empacota seis alvos, instala o
mesmo tarball em Linux/macOS/Windows sem scripts de lifecycle e publica usando
`secrets.NPM_TOKEN`. Tags `vX.Y.Z` publicam em `latest`; prereleases em `next`.
O workflow de release também chama essa etapa após publicar o GitHub Release,
inclusive quando a tag é criada pelo próprio Actions. O workflow npm aceita uma
tag existente por dispatch para retomar uma publicação que falhou; uma versão
já publicada só é aceita novamente se o tarball tiver a mesma integridade SHA-512. A versão npm é imutável e corresponde à tag e ao SHA embutido no cliente.
