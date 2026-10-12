# Configurar trusted publishing do wksctl

Nas configurações do pacote **wksctl** no npm, crie esta conexão:

| Campo | Valor |
| --- | --- |
| Publisher | GitHub Actions |
| Label | wks |
| Organization or user | A1b3rt0M3rcad0 |
| Repository | wks |
| Workflow filename | npm.yaml |
| Environment name | Deixar vazio |
| Allow npm publish | Marcar |
| Allow npm dist-tag | Deixar desmarcado |

Clique **Set up connection**. O nome do workflow é apenas `npm.yaml`, com essa
extensão e sem o caminho `.github/workflows/`. A permissão de `npm dist-tag`
controla comandos separados de alteração de tags, que este workflow não executa;
`npm publish --tag latest|next` define a tag durante a própria publicação.

O workflow usa runner hospedado no GitHub, Node 24, npm 11.9.0 e
`id-token: write` somente no job de publicação. `npm publish --provenance`
usa automaticamente OIDC; o workflow não recebe nem exige `NPM_TOKEN`.
Nenhum GitHub Environment é usado, portanto o campo correspondente fica vazio.

As releases automáticas disparam `npm.yaml` por `workflow_run` após **Release WKS**
concluir com sucesso. A versão e o commit vêm do manifest qualificado desse run e
são comparados à tag imutável. Releases sem candidato não iniciam publicação.
Esse workflow também aceita pushes de tags e dispatch manual de uma tag existente.
Todas as formas executam o próprio `npm.yaml`: a conexão não precisa autorizar
um workflow chamador como `release.yaml`. As publicações da mesma versão são
serializadas e só reutilizam uma versão existente com integridade idêntica.

A versão `1.1.0` já publicada continua disponível. Reexecutar o workflow para essa
versão valida pacote, instalação pública e retomada, mas não faz uma publicação
nova e não demonstra autenticação OIDC. Uma publicação nova por OIDC depende de
salvar a conexão acima no npm. Não é necessário criar outra versão só para
configurar a conexão.

Referência: https://docs.npmjs.com/trusted-publishers/.

## Validação concluída

A conexão foi validada por uma publicação nova via OIDC em 2026-10-12:
`wksctl@1.1.1-rc.1`, no canal `next`. O registry identifica o publicador como
**GitHub Actions**, e as attestations de publicação e provenance SLSA vinculam
o pacote exato a `.github/workflows/npm.yaml`. O canal estável `latest` continua
em `1.1.0`. O workflow de publicação e verificação pública terminou com sucesso.
Veja [evidência da validação](npm-oidc-validation-2026-10-12.md).
