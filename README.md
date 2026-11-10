# Cortaí ✂️

Clone local do OpusClip: você joga um vídeo longo (live, podcast, VOD) e recebe cortes verticais 9:16 prontos para TikTok/Reels/Shorts, com o **Claude** escolhendo os melhores momentos.

## Como funciona

1. **Entrada**: link (YouTube, Twitch, TikTok… via `yt-dlp`), upload ou caminho de arquivo no PC (sem copiar).
2. **Transcrição**: `faster-whisper` local na GPU, com tempo de cada palavra.
3. **Sinais de áudio**: picos de grito/risada, som alto sem fala (explosão, gol, morte no jogo) e silêncios viram marcadores na transcrição — em live de jogo o momento bom quase nunca é uma frase.
4. **Claude, etapa 1 — candidatos**: lê transcrição + marcadores e propõe o dobro de candidatos, cada um com o segundo do pico.
5. **Claude, etapa 2 — revisão visual**: vê uma folha de frames de cada candidato (com o segundo marcado), descreve o que acontece na tela, descarta menu/treino/tela parada, ajusta início/fim pelo evento visual e dá notas de gancho, ritmo, graça e compartilhamento.
6. **Estilos de corte**: um estilo embutido (cortes de streamer BR, destilado de cortes do alanzoka) e estilos aprendidos — cole links de cortes que você admira e o Claude escreve um guia de como cortar igual.
7. **Render** (ffmpeg, NVENC quando disponível) em 1080x1920:
   - **Tela dividida**: webcam em cima, jogo embaixo (vídeo horizontal com rosto).
   - **Seguir rosto**, **posição fixa** (você escolhe) ou **vídeo inteiro** com fundo desfocado.
   - **Legendas animadas** com presets (destaque, marca-texto, uma palavra por vez, minimalista), posição e cor; **título fixo no topo** opcional.
8. **Editor de clipe**: prévia do trecho com folga, timeline com o volume (arraste as bordas), atalhos I/O/espaço/setas, transcrição clicável, correção de palavras da legenda (duplo clique), enquadramento, legendas e título — depois "Salvar e renderizar".
9. Também dá para pedir novos cortes ao Claude com outras instruções (reaproveita transcrição e sinais) e analisar só um trecho do vídeo.

## Rodar

```bash
pip install -r requirements.txt
python run.py
```

Abre em http://127.0.0.1:8765. Precisa de `ffmpeg`/`ffprobe` no PATH. Os projetos ficam em `jobs/`.

## Escolhendo como falar com o Claude

| Modo | O que precisa | Custo |
|---|---|---|
| **Claude Code** (`cli`) | Claude Code instalado e logado | usa sua assinatura Pro/Max |
| **API** (`api`) | `ANTHROPIC_API_KEY` no ambiente | cobrado por token na API |
| **Manual** | nada | você copia o prompt para o claude.ai e cola a resposta de volta |

O modo **Automático** usa API se houver chave, senão Claude Code se estiver logado, senão Manual.

Para usar sua assinatura com o Claude Code:

```bash
npm install -g @anthropic-ai/claude-code
```

```bash
claude auth login --claudeai
```

Autorize no navegador (o login do app desktop do Claude não vale para o CLI). O app chama `claude -p` em modo headless, sem ferramentas, com JSON Schema para a resposta.

Variáveis opcionais: `CORTAI_MODEL` (padrão `claude-opus-5-5`), `CORTAI_EFFORT` (padrão `high`), `CLAUDE_CLI` (caminho do executável), `CORTAI_PORT`.

## Limitações conhecidas

- O rastreamento segue o **maior rosto**; em podcasts com 2 pessoas ainda não detecta quem está falando.
- O modo manual (sem Claude Code/API) faz só a etapa 1, sem revisão visual nem aprendizado de estilo.
- O detector de rosto (Haar) perde rostos de perfil; nesses trechos a câmera mantém a última posição.
- Um job é processado por vez.
