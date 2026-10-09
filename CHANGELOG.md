# Changelog

## 0.3.0

- Runtime e perfis nativo/OCR, Docling e áudio/vídeo em pacotes, executáveis e imagens separados.
- API/Core sem dependências de decoders ou workers; fila persistente com migração 0007.
- Claims, retries e recuperação de leases filtrados por fila; escala independente em Compose.
- Release dos seis wheels e quatro imagens, com dependências por componente e qualificação real.

## 0.2.0

- Primeira conta criada no navegador, sem conta ou senha humana pré-configurada.
- Login com username/senha e sessão HttpOnly.
- Token de recuperação mostrado uma vez, com download/cópia; recuperação troca senha,
  consome o token e encerra sessões anteriores. Rotação autenticada exige a senha atual.
- Migração 0006 com hashes de senha/token, bootstrap concorrente seguro e limites compartilhados.
- Releases por SemVer em master, tags e disparo manual, com CI reutilizável, wheels/fontes,
  checksums, imagem GHCR amd64/arm64 e proteção contra sobrescrita de versões.
