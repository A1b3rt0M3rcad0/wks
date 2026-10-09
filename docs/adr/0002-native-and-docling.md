# ADR 0002 — Perfil native e adapter Docling

Status: aceito. O plano propõe Docling para estrutura multimodal. O WKS oferece esse adapter,
com weights fixados, e também um perfil native CPU que funciona sem baixar modelos de layout.
Esse perfil preserva texto nativo, OCR seletivo, figuras e páginas, e declara limites de
estrutura para fórmulas/tabelas. Falha de Docling cai explicitamente no native, sem inventar
layout nem interromper texto previamente pesquisável. Cada perfil registra producer/config.

Não são dois índices ou domínios diferentes: os dois adaptadores produzem o mesmo JSON
canônico, Markdown e locators. Reprocessar com outro perfil publica nova representação.
Quadro de suporte real é registrado por fixture/teste, não por lista de formatos da biblioteca.
