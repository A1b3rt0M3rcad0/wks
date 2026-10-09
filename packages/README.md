# Pacotes WKS

A organização segue as fronteiras do WOS, consultado somente para leitura. O workspace
Python contém seis pacotes instaláveis e versionados juntos:

| Pacote | Responsabilidade | Executável |
| --- | --- | --- |
| `wks-core` | Domínio, Service, autorização, persistência, fila e identificação de uploads | — |
| `wks-api` | HTTP/MCP, contas, contratos, frontend e manutenção | `wks api` |
| `wks-worker` | Claim, heartbeat, fencing, subprocessos limitados e publicação atômica | `wks-worker` |
| `wks-worker-native` | Texto, PDF, OCR, Office, imagens e HTML | `wks-worker-native` |
| `wks-worker-docling` | Layout/tabelas de PDF e Office, com fallback nativo explícito | `wks-worker-docling` |
| `wks-worker-media` | Áudio, transcrição local e frames/transcrição de vídeo | `wks-worker-media` |

A API depende somente do Core entre os pacotes WKS. Core não importa API, workers,
transportes ou decoders. O runtime dos workers depende do Core; os perfis dependem do
runtime. Docling depende também do nativo para preservar evidência quando o modelo falha.
Áudio/vídeo não instala o pacote nativo. Nenhum worker instala FastAPI, MCP ou o frontend.

`uv sync --frozen` na raiz instala o workspace de desenvolvimento; isso não define as
fronteiras de produção. Para instalar apenas um componente, use `uv sync --frozen
--no-dev --package wks-api` ou `--package wks-worker-native`, por exemplo. Docling usa o
extra `models`; mídia usa `asr`. A raiz oferece `--extra docling --extra asr` para os testes.

Cada executável de perfil aceita somente sua fila. `wks-worker --queues native,media`
é útil para desenvolvimento quando ambos os pacotes estão instalados; o runtime rejeita
filas cujo pacote não está instalado antes de assumir qualquer job. `wks worker` e
`wks reconcile` foram substituídos por `wks-worker` e `wks-worker --reconcile`.

Os assets permanecem no wheel da API. Migrações, Compose e testes de integração ficam
na raiz, pois coordenam o schema e o deployment. Node é somente ferramenta de testes.
Veja [operação dos workers](../docs/WORKERS.md) para instalação, filas, modelos e escala.
