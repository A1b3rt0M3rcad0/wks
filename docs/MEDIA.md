# Entrega multimodal

Imagens extraídas e páginas PDF são Assets com checksum, MIME, locator e URI estável. Pesquisa
textual retorna refs de figuras da mesma página; read retorna Blocks/Markdown e relação
documental explícita. Fórmulas/tabelas ilegíveis permanecem preservadas nas páginas originais.
Office mantém figuras; o perfil native indica região desconhecida sem inventar bbox.

Áudio preserva original e indexa transcrição com `start_ms/end_ms`; vídeo preserva original,
transcreve faixa de áudio e oferece frames com time_ms/sample_index. Um frame não é análise
de movimento. ASR não representa sons além da fala. MIME e hash podem ser verificados pelo
cliente no download autorizado. Sem transcritor, coverage=pendente e bytes continuam legíveis.

HTTP pode renderizar/reproduzir bytes no app. MCP devolve resource/metadata e recurso pequeno;
a materialização até um modelo não foi homologada por este serviço. Um consumer de texto
recebe fallback explícito. O WKS não transforma falha de delivery em descrição inventada.

O corpus de teste é gerado pelos autores do WKS (CC0), inclui PDF misto, DOCX/PPTX, imagem,
texto português, áudio sintetizado e vídeo. `tests/fixtures/manifest.json` fixa hashes.
Os modelos baixados mantêm sua própria licença; não são empacotados nos fixtures.
