# CLI npm 1.1.0 — publicação e verificação

Publicado em 2026-10-12: https://www.npmjs.com/package/wksctl/v/1.1.0.
Tag imutável `v1.1.0`, source `8c3d91d0ad246a249054afd478b50e808bc019f2`.
O npm `latest` aponta para `1.1.0`. O registry inicialmente apresentou o placeholder
`0.0.0-stage`; a disponibilidade pública foi confirmada após o processamento.

## Uso

```sh
npm install -g wksctl
wksctl version
wksctl skill install --directory ./meu-projeto
# Alternativa sem instalação global:
npx --yes wksctl@1.1.0 skill install --directory ./meu-projeto
```

Node 20+ é necessário para o launcher. Não há dependências de pacote, scripts de
lifecycle ou downloads adicionais de executável. O tarball inclui a Skill, licenças,
notices e seis binários Go: Linux/macOS/Windows, x64/ARM64 (18,4 MB compactado).
A Skill é instalada em `.agents/skills/wks/SKILL.md`. A instalação é offline após
instalar o pacote, idempotente e preserva skills personalizadas e `AGENTS.md`.

## Evidência

- Workflow de publicação: https://github.com/A1b3rt0M3rcad0/wks/actions/runs/38273455515.
- Tarball exato instalado e exercitado no Actions em Linux/macOS/Windows, sem scripts de lifecycle.
- CLI/SDK Go passaram em testes com race detector; lint passou.
- Registry público devolveu `wksCommit` correspondente à tag; SHA-512 integral coincide
  com o artefato qualificado. SHA-1 npm: `617f015d1d3f710f26ac573d5dee9a32dc98be27`.
- Instalação pública via npm e execução via npm exec/npx verificadas no cloud.
- Skill instalada via npx e comparada byte a byte à Skill distribuída, sem criar workspace/conta.
- Testes locais do guard de publicação e identidade de release: 18 passaram;
  dois testes completos de empacotamento ficaram a cargo do gate existente no Actions.
- Guard de republicação executado contra `1.1.0` pública: reconheceu conteúdo idêntico sem publicar novamente.

A publicação inicial usou `secrets.NPM_TOKEN` somente no job de publicação. Naquela configuração, releases automáticas chamavam
o workflow npm após o GitHub Release: `latest` para estáveis e `next` para prereleases.
O workflow aceita dispatch de uma tag existente e só reutiliza uma versão publicada
quando a integridade do tarball é idêntica. A etapa final aguarda a disponibilidade
pública, compara SHA-512 e testa a instalação pelo registry.

A configuração atual foi migrada para OIDC/trusted publishing sem token persistente.
O workflow npm agora tem execução própria após a release, para manter uma única
identidade `npm.yaml`. Consulte [configuração atual](NPM_PUBLISHING.md).
