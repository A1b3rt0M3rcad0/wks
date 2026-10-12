# Trusted publisher npm validado — 2026-10-12

Publicação nova: `wksctl@1.1.1-rc.1` no canal `next`.
O canal `latest` continua em `1.1.0`; instalar `wksctl` sem especificar canal
continua entregando a versão estável.

- Workflow concluído: https://github.com/A1b3rt0M3rcad0/wks/actions/runs/38280821101.
- Pacote público: https://www.npmjs.com/package/wksctl/v/1.1.1-rc.1.
- Tag `v1.1.1-rc.1`, fonte do cliente `53e11bdf97fbd66064e42896ea4c943d17fe9b0d`.
- Fonte do workflow/script de publicação: `d109d5593f44300e59301773153674a504a99d36`.
- O npm registra `_npmUser.name` como **GitHub Actions**. O workflow não recebe `NPM_TOKEN`.
- Provenance: https://registry.npmjs.org/-/npm/v1/attestations/wksctl@1.1.1-rc.1.
- Transparência Sigstore: https://search.sigstore.dev/?logIndex=3231724722.

As duas attestations públicas — publicação do registry e provenance SLSA v1 —
têm o SHA-512 do mesmo tarball qualificado no Actions. O statement SLSA identifica
`https://github.com/A1b3rt0M3rcad0/wks`, `.github/workflows/npm.yaml` e
`refs/heads/master`. O digest público também foi comparado ao artefato baixado do
Actions. Isso é uma publicação nova por OIDC, não a reutilização de uma versão
já existente.

O mesmo pacote foi instalado e exercitado em Linux/macOS/Windows. A instalação
pública adicional no cloud passou nos contratos de versão, execução nativa,
instalação da Skill, idempotência, preservação de arquivos personalizados e
propagação de exit codes. Lint e os 12 testes locais do script/workflow passaram.

O script de publicação agora passa um caminho absoluto ao npm. Esse detalhe
impede que o npm interprete `npm-tarball/arquivo.tgz` como um repositório Git;
um teste cobre explicitamente o caso relativo. A primeira tentativa foi rejeitada
antes de publicar e a candidata foi retomada sem mudar sua tag imutável.
