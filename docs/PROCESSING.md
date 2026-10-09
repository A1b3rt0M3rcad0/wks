# Processamento e cobertura

O original é confirmado antes do job. Worker reivindica leases PostgreSQL, verifica fencing
e integrity, materializa cópia local, extrai em subprocesso limitado e publica structure,
assets, segments, pointer atual e evento na mesma transação. Reprocessar produz nova
Representation, não reescreve SourceVersion. Publições anteriores continuam legíveis por ID.

| Entrada | Processador | Proveniência e limites |
|---|---|---|
| TXT/MD/JSON/YAML/CSV | UTF-8 validado, normalização/linhas/tabelas CSV | Locators de linha/row; original intacto |
| PDF native | pypdf + PDFium + Tesseract seletivo | Texto nativo preservado, OCR para páginas sem texto; figuras e página renderizada |
| PDF docling | Layout/estrutura Docling com artefatos fixados | Tabelas/layout e figuras; fallback native explicitamente avisado se provider falhar |
| DOCX/PPTX | Native Office ou Docling | Parágrafos, tabelas, figuras, seções/slides |
| Imagem | Pillow + Tesseract | Bytes, MIME e OCR; não declara compreensão visual |
| Áudio | faster-whisper local + VAD | ASR em CPU, timestamps, logprob; sem interpretação de som não verbal |
| Vídeo | FFmpeg + ASR | Frames limitados com timestamps reais, áudio quando disponível |
| HTML | Trafilatura | Captura preservada; texto externo tratado como dados |
| Bookmark | Metadata | Nenhum fetch; fonte pesquisável por título/descrição |
| Outros | Unsupported | Bytes retidos; nenhuma extração fictícia |

Flags ASR/video/capture/enrichment são false/none por padrão. Formats indisponíveis continuam
armazenáveis. Native PDF avisa que tabela/fórmula complexa está preservada como página;
layout estruturado exige perfil Docling qualificado. OCR pode falhar independentemente do
texto nativo. ASR tiny serve como perfil CPU de referência, não promessa de acurácia universal.
Transcrição carrega qualidade não verificada e aviso de probabilismo; segmentos de baixa
logprob são marcados uncertain. Revisões de modelos/configuração exigem reprocessamento.

OOM/timeouts/ZIP bombs/MIME inválido não viram retry infinito. Backoff é limitado; erro
transitório permite retry_wait, corrupção é terminal. Grupo de processos é encerrado em timeout.
Reclaim de lease expirado incrementa geração; worker antigo não consegue publicar. Source
revogada/deletada cancela jobs e prevalece mesmo com uma antiga geração de índice.

Enrichment opcional aceita só imagens pequenas, endpoint HTTPS fixo, operation key estável
e saída limitada com producer. Novos blocos têm origin `ai_description`; falha vira warning
e mantém texto/bytes. Consentimento e endpoint/provider devem ser operados pelo cliente.
