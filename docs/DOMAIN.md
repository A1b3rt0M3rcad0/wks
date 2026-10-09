# Domínio e referências

`Client` é uma identidade de serviço, não login de aluno. `Namespace` é a fronteira de
ownership. `Collection` seleciona Sources do mesmo namespace, sem duplicar bytes.
`Source` é a identidade lógica; `SourceVersion` fixa o original/captura; `ProcessingRun`
identifica uma execução; `Representation` fixa estrutura, Markdown, cobertura e produtor.
`Segment` aponta para Blocks e Assets; `Blob` mantém hash, tamanho, MIME e chave opaca.

Referências persistentes:

```text
wks://sources/{source_id}/versions/{source_version_id}
wks://representations/{representation_id}/blocks/{block_id}
wks://representations/{representation_id}/segments/{segment_id}
wks://assets/{asset_id}
```

Nenhuma URI confere permissão. Não contém bearer, URL assinada ou chave de bucket. IDs
históricos não fazem fallback para uma versão recente. Reprocessamento cria Representation;
mudança de original cria SourceVersion. Receipts fixam o resultado de cada mutação.

Fonte: `registered`, `stored`, `revoked`, `deleted`. Job: `queued`, `running`, `retry_wait`,
`succeeded`, `partial`, `failed`, `cancelled`. Availability: `metadata_only`, `text_partial`,
`text_ready`, `unsupported_processing`. São conceitos independentes. Cobertura guarda fatos
por modalidade, não porcentagens de compreensão. Bookmarks permanecem `bookmark_only`.

Estrutura mantém tipo de bloco, ordem, parent, locator, origem textual, dados de tabela e
refs explícitas de mídia. Markdown deriva dessa estrutura; código não reconstrói proveniência
a partir de Markdown. Texto recuperado é marcado `untrusted_source_data`.
