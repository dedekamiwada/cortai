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
10. **Zoom nas reações**: o Claude marca os segundos em que a cara do streamer reage mais forte e o vídeo dá um "punch-in" ali.
11. **Palavras-chave** destacadas na legenda em outra cor, **descrição pronta para postar** e **legenda .srt** de cada clipe.
12. **Remover pausas mortas** (jump cuts) sem cortar silêncio com risada/som do jogo nem a reação.
13. **Estilo de todos os clipes** de uma vez (e salvar como padrão), **clipe manual** pela busca na transcrição, **baixar todos (.zip)** com `posts.txt`.

## Títulos

As regras de título ficam em `cortai/titles.py` (vêm da skill `titulos-virais-clips`, feita a partir de ~400 Shorts de jogos com e sem views). Na revisão visual o Claude devolve 5 opções de fórmulas diferentes (`title_options`, com fórmula F1–F10 e nota). O código descarta as que quebram regras duras (mais de 60 caracteres, hashtag, "kkkk", "!!!", mais de 1 emoji, "clipe/corte/gameplay/melhores momentos" fora de compilação) e fica com a de maior nota. As outras opções aparecem como sugestões no campo de título do editor.

Para projetos que já estão prontos, o botão **🏷 Gerar títulos** pede ao Claude só títulos e posts novos, usando a descrição da revisão e a fala de cada clipe, sem refazer os cortes. O título anterior continua como sugestão no editor; clipes com o título gravado no vídeo são renderizados de novo.

Aprendizado: cada título vai para `jobs/_titulos/titulos.csv` (abre no Excel). Depois de postar, preencha a coluna `views` (aceita `12000`, `12.000`, `12k`, `1,2 mil`). Quando houver pelo menos 2 fórmulas com 3 posts medidos ou mais, o prompt passa a dizer ao Claude quais fórmulas rendem mais no seu canal. Feche o CSV no Excel antes de rodar um job, senão a gravação daquele job é pulada.

## Capa (thumbnail)

Cada clipe sai com uma **capa pronta** em 1080x1920 (`capa/capa.jpg`), feita do **momento ápice** com o **título** por cima:

- **Quadro**: a reação do rosto logo depois do pico que o Claude marcou (sem reação, o próprio pico; sem pico, o som mais alto do trecho). Entre o quadro certo e os vizinhos fica o mais nítido, porque no pico o jogo costuma estar borrado. Os quadros vêm do render, **sem legenda nem título** (2 por segundo, em `capa/quadros/`).
- **Texto**: o título do clipe, com a palavra-chave do Claude em destaque (sem ela, a última palavra). Fica acima ou abaixo do rosto e dentro da zona segura do TikTok (o perfil corta em ~3:4 e o título automático do app cobre a parte de baixo).
- **10 estilos**: Impacto, Adesivo (contorno duplo), Neon, Grito, Faixa vermelha, Fita preta, Quadrinho, Marca-texto, Caixa branca e Minimalista (fontes Anton, Bangers e Montserrat Black). As fontes (licença OFL) ficam em `static/fonts`; o desenho é feito pelo ffmpeg + libass, como as legendas.
- **Editor** (🖼 Editar capa no clipe): texto (`*palavra*` destaca, Enter quebra linha, as opções de título do Claude viram atalhos), estilo, tamanho, inclinação, sombra, cores, assinatura (ex.: `@seu_perfil`), escolha do quadro (⭐ = ápice), zoom e posição da imagem. Arraste na prévia para mover o texto.
- Se você não mexeu no texto ou no quadro, eles acompanham o clipe: trocar o título (no editor ou em **🏷 Gerar títulos**) ou o trecho refaz a capa sozinho.
- **No TikTok**: "Editar capa" → "Enviar" e escolha o `capa.jpg`. Ou ligue **Gravar a capa no fim do vídeo** (0,4 s no último quadro, para escolher arrastando o seletor até o fim); o clipe sem a capa fica em `sem_capa.mp4` e é o que o compilado e a pós-edição usam.
- O `.zip` leva a capa de cada clipe. Projetos antigos: botão **🖼 Gerar capas** (live vertical: quadros do vídeo original; horizontal: do clipe pronto, com a legenda dele, até salvar o clipe de novo).
- Padrões em **Mais opções** do formulário (ligar/desligar, estilo, assinatura, capa no vídeo) ou marcando "usar como padrão" no editor.

## Melhores momentos da partida

No formulário, **Tipo de projeto → Melhores momentos da partida (1 vídeo)**. Em vez de vários cortes soltos, sai um vídeo só com os melhores lances da partida, em **duas versões**: short vertical de 60–90 s e horizontal (YouTube), cada uma com um **roteiro de narração** (.txt e .srt com o tempo de cada fala) para você gravar por cima no CapCut.

- **Escolher os lances pela fala + imagem** (usa a transcrição e os picos de áudio) ou **só pela imagem** (sem transcrição: o Claude assiste à partida em folhas de frames e usa os picos do som do jogo).
- O Claude monta a história (começo → lances → momento decisivo → resultado), dá um rótulo a cada lance ("#3 · VIRADA") e escreve as falas da narração. Os lances aparecem como clipes normais: dá para editar um e clicar em **↻ Remontar vídeos**.
- **Separar por partida**: numa live longa (ou pelo botão **🗺 Separar por partida** de qualquer projeto), o Claude mapeia a live pelo HUD do jogo — início, fim, placar, resultado e um resumo de cada partida. Você marca as partidas que quer e clica em **Gerar vídeos das partidas marcadas**; cada uma vira um projeto, reaproveitando a transcrição da live.
- Limitação: com uma live vertical (cena vertical do OBS), a versão horizontal mostra o vídeo vertical no meio com fundo desfocado.

## Pós-edição (drama num clipe pronto)

Em `pos.html` (link "✨ Pós-edição" no topo, ou o botão **✨ Pós-edição** em cada clipe) você abre um vídeo já cortado — do Cortaí ou de fora, mesmo com legenda e título gravados — e o Claude planeja a pós-edição:

- Ele lê a fala (tempo de cada palavra), os picos e silêncios e folhas de frames (1 por segundo), e devolve **beats**: zoom punch no rosto, zoom lento no silêncio dramático, tremor, **flash de texto** (só texto, no máximo 1), **selo** (1–2 palavras, tipo "LAVA"), emote, efeito sonoro e as partes da **trilha** (tensão, corte seco, triste, épico, cômico).
- Você revisa a tabela (tempo · o que acontece · edição), desliga o que não quiser, corrige os textos e renderiza. Efeito sonoro que cairia por cima da fala já vem desligado.
- O render é só ffmpeg: voz destacada, SFX a -6 dB, trilha que abaixa sozinha quando você fala (ducking) e volume final a -14 LUFS. Saem **duas versões: com trilha e sem trilha** (para pôr a música do TikTok pela biblioteca licenciada do app).

**Biblioteca** (`jobs/_biblioteca/` ou `Vídeos\Cortai\_biblioteca`, ou a pasta que você escolher na tela): `sfx/`, `emotes/` e `musicas/<clima>/`. Coloque ali só arquivos que você tem direito de usar; o Claude só escolhe o que existe. Na primeira vez o Cortaí cria um kit de SFX original (`cortai-*.wav`). Sem música de **tensão** ou **triste**, ele gera uma trilha original; épico e cômico ficam mudos sem arquivo.

A opção **Voz destacada** (EQ que tira o grave abafado e realça a presença, compressor e limitador) também vale para os cortes normais e vem ligada por padrão.

## Estúdio (editor sem IA)

Em `studio.html` (link "Estúdio" no topo) fica um editor de vídeo completo que roda 100% local — as mídias são usadas direto do seu PC, nada vai para a internet:

- Projetos 9:16, 16:9, 1:1, 4:5 ou 3:4; importar por caminho, arrastando ou enviando arquivos (vídeo, imagem, música).
- Timeline com 4 faixas: **principal** (sequência magnética), **sobreposição** (picture-in-picture), **texto** e **áudio**.
- Dividir (S), aparar pelas bordas, reordenar, duplicar, excluir, ímã, zoom da timeline, desfazer/refazer, salvamento automático.
- Velocidade (0,25x–4x), volume/mudo, fades, enquadramento (preencher com zoom/posição, encaixar, fundo desfocado), brilho/contraste/saturação e looks prontos.
- 18 transições (esmaecer, deslizar, cortina, círculo, zoom, pixelizar…), textos com fonte, contorno, caixa e animação (pop, zoom, subir, esmaecer) — arrastáveis direto na prévia.
- Prévia em tempo real com proxies leves; exportação pelo ffmpeg (NVENC quando houver GPU) em 1080p ou 720p.
- Botão **🎬 Estúdio** em cada clipe da IA abre aquele trecho no editor para finalizar à mão.

## Rodar

```bash
pip install -r requirements.txt
python run.py
```

Abre o Cortaí numa janela própria (WebView2, via `pywebview`); sem o `pywebview` instalado, abre http://127.0.0.1:8765 no navegador. Com `CORTAI_NO_BROWSER=1` sobe só o servidor. Precisa de `ffmpeg`/`ffprobe` no PATH. Os projetos ficam em `jobs/`.

## Versão portátil (Windows)

Rode `build.bat` para gerar `dist\Cortai\`, uma pasta de cerca de 1,5 GB com o `Cortai.exe`, Python, dependências, ffmpeg e o que a GPU precisa. Copie a pasta inteira, não só o `.exe`. Não precisa instalar Python nem ffmpeg no PC de destino.

- Abra o `Cortai.exe`: o Cortaí abre como programa, em janela própria, sem navegador nem console. Abrir de novo só traz a janela para a frente.
- Fechar a janela encerra o Cortaí. Se houver corte ou exportação em andamento, ele pergunta antes, e o ffmpeg/Claude em execução é encerrado junto.
- Downloads de clipes abrem o "Salvar como" do Windows. Mensagens de erro ficam em `%LOCALAPPDATA%\Cortai\cortai.log`.
- Precisa do WebView2 Runtime, que já vem no Windows 11 e no Windows 10 atualizado.
- Os projetos ficam em `Vídeos\Cortai`, fora da pasta do programa. Para usar outro lugar, crie ao lado do `.exe` um `dados.txt` com o caminho da pasta.
- O modelo do Whisper (1,6 GB no `large-v3-turbo`) é baixado no primeiro uso.
- Para a IA escolher os cortes, cada PC precisa do Claude Code instalado e logado ou de `ANTHROPIC_API_KEY`. O Estúdio não precisa de nenhum dos dois.
- Sem GPU NVIDIA compatível, a transcrição usa a CPU e fica bem mais lenta.

A pasta `dist/` não vai para o repositório. Antes de distribuir a pasta para outras pessoas, confira as licenças do ffmpeg e das bibliotecas da NVIDIA.

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
