"""Seleção de clipes pelo Claude, em duas etapas.

1. Candidatos: o Claude lê a transcrição intercalada com sinais de áudio (gritos, risadas,
   som do jogo, silêncios) e propõe o dobro de candidatos.
2. Revisão visual: para cada candidato o Claude vê uma folha de frames do trecho, confirma o
   que acontece na tela, descarta o que não presta e ajusta início/fim pelo evento visual.

Backends (mesmos prompts e JSON Schemas):
  - "cli":    Claude Code headless (`claude -p`) — usa sua assinatura Pro/Max.
  - "api":    API da Anthropic (ANTHROPIC_API_KEY) via SDK oficial.
  - "manual": você cola o prompt no claude.ai (só a etapa 1, sem revisão visual).
"""
from __future__ import annotations

import base64
import glob
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from . import signals, titles

MODEL = os.environ.get("CORTAI_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("CORTAI_EFFORT", "high")


# ------------------------------------------------------------------ modelos e schemas

class Clip(BaseModel):
    title: str
    start: float
    end: float
    hook: str = ""
    reason: str = ""
    score: int = Field(default=50)
    hashtags: list[str] = []
    peak: float | None = None
    visual: str = ""
    scores: dict[str, int] = {}
    candidate: int | None = None
    keep: bool = True
    reactions: list[float] = []
    keywords: list[str] = []
    post: str = ""
    title_options: list[dict] = []


class ClipPlan(BaseModel):
    clips: list[Clip]


_CLIP_FIELDS = {
    "title": {"type": "string", "description": "Título curto, no idioma do vídeo"},
    "start": {"type": "number", "description": "Segundo de início no vídeo original"},
    "end": {"type": "number", "description": "Segundo de fim no vídeo original"},
    "hook": {"type": "string", "description": "O que prende nos primeiros segundos"},
    "reason": {"type": "string", "description": "Por que funciona como clipe (português)"},
    "score": {"type": "integer", "description": "Potencial viral de 0 a 100"},
    "hashtags": {"type": "array", "items": {"type": "string"}},
}

CANDIDATES_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["clips"],
    "properties": {"clips": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": [*_CLIP_FIELDS, "peak"],
        "properties": {**_CLIP_FIELDS,
                       "peak": {"type": "number", "description": "Segundo do acontecimento principal"}},
    }}},
}

REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["clips"],
    "properties": {"clips": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["candidate", "keep", "visual", "scores", "peak", "reactions", "keywords", "post",
                     "title_options", *_CLIP_FIELDS],
        "properties": {
            "title_options": titles.TITLE_OPTIONS_FIELD,
            "candidate": {"type": "integer", "description": "Número do candidato revisado"},
            "keep": {"type": "boolean", "description": "false se a tela não sustenta o corte"},
            "visual": {"type": "string", "description": "O que se VÊ acontecendo (jogo e câmera)"},
            "peak": {"type": "number", "description": "Segundo do acontecimento principal"},
            "reactions": {"type": "array", "items": {"type": "number"},
                          "description": "Segundos (vídeo original) das reações faciais mais fortes, no máximo 2"},
            "keywords": {"type": "array", "items": {"type": "string"},
                         "description": "1 a 4 palavras ditas no trecho que carregam a graça, para destacar na legenda"},
            "post": {"type": "string", "description": "Descrição para postar, no idioma do vídeo, com 3 a 5 hashtags"},
            "scores": {
                "type": "object", "additionalProperties": False,
                "required": ["hook", "flow", "value", "trend"],
                "properties": {k: {"type": "integer"} for k in ("hook", "flow", "value", "trend")},
            },
            **_CLIP_FIELDS,
        },
    }}},
}

# compatibilidade: modo manual e respostas antigas usam o formato simples
SCHEMA = CANDIDATES_SCHEMA


# ------------------------------------------------------------------ prompts

OUTPUT_RULES = """
Regras de saída:
- Use os segundos da transcrição (início de uma linha/palavra para `start`, fim para `end`).
- Respeite a duração mínima e máxima pedida. Clipes não podem se sobrepor.
- Títulos, ganchos e hashtags no idioma do vídeo. `reason` em português.
- `score` é sua estimativa honesta de 0 a 100.
- Prefira poucos cortes fortes a muitos fracos.
- Instruções extras do usuário e o guia de estilo têm prioridade sobre os critérios gerais."""

CONTENT = {
    "game": """Você é um editor sênior de cortes de lives de jogos para TikTok, Reels e Shorts.

Em live de jogo o que viraliza é um ACONTECIMENTO (no jogo ou na câmera) + a REAÇÃO do streamer — não uma \
frase solta. A transcrição só mostra fala, então ela vem intercalada com marcadores de áudio:
- 😱 susto provável: silêncio e de repente som alto (jumpscare, explosão) — o candidato mais forte a pico;
- 💥 som alto SEM fala: risada, grito, explosão/gol/morte no jogo — forte candidato a pico;
- 🎮 intervalo sem fala mas com som: reação, risada longa ou jogo acontecendo;
- 🤐 silêncio: choque, concentração ou tela parada;
- 🔊 grito/fala muito alta.
Frases curtas repetidas ("não não não", "corre corre", "ERREI") e xingamentos de surpresa também marcam pico.

Quando o vídeo tem placar de jogo, a transcrição traz também eventos lidos NA TELA, que são as âncoras mais \
confiáveis (o tempo é exato, ±2 s):
- ⚽ gol do time do streamer e 🥅 gol sofrido, com o placar depois do gol e até quando vai o replay automático;
- 🏁 começo de partida e ⏱ fim do tempo normal;
- 📈 reação forte: volume muito acima do normal DELE naquele trecho da live (z alto = reação maior).

Para cada corte:
- identifique o segundo do acontecimento principal (`peak`) — se houver um ⚽/🥅/📈 ali, use o segundo dele;
- pelo menos 1/3 dos candidatos devem ser ancorados num evento de jogo (gol, gol sofrido, defesa, falha), \
mesmo que ninguém fale nada: em live de jogo o lance sem fala é o que a transcrição esconde;
- comece 5 a 12 s antes do pico, no começo da jogada que leva a ele, numa fronteira de frase ou silêncio (sem \
introdução e sem cortar palavra);
- termine quando a reação acabar — não corte no meio da risada. Depois de um gol o jogo passa um replay \
automático: termine quando a reação dele acabar (normalmente 4 a 10 s depois do gol), não no fim do replay;
- desconfie de trechos longos de explicação, leitura de chat, agradecimento de presente ou papo sem evento.""",
    "talk": """Você é um editor sênior de vídeos curtos (TikTok, Reels, Shorts) que corta podcasts, \
entrevistas e conversas.

Critérios: gancho forte nos primeiros 3 s, nunca começar no meio de uma frase; trecho autocontido; payoff \
(conclusão, punchline, revelação) antes do fim; prefira emoção, humor, opinião forte, história com reviravolta \
ou dica prática. A transcrição traz marcadores de áudio (risadas, gritos, silêncios) que ajudam a achar reações. \
Use `peak` para o segundo da frase ou reação mais forte.""",
}

REVIEW_SYSTEM = """Você é o editor-chefe revisando candidatos a corte. Agora você VÊ o vídeo: para cada \
candidato há uma imagem em grade com quadros do trecho, cada quadro marcado com o segundo do vídeo original \
(canto superior esquerdo).

Para cada candidato:
1. Descreva em `visual` o que realmente acontece na tela (jogo e câmera do streamer), em uma ou duas frases.
2. `keep=false` se a imagem não sustenta o corte: menu, loja, carregamento sem reação, treino/aquecimento, \
tela parada, jogo sem nada acontecendo enquanto ele só conversa — a não ser que a reação na câmera seja o destaque.
3. Ajuste `start` para que o acontecimento visual caia depois de 5 a 10 s de contexto (o suficiente para \
entender o lance) e `end` para logo depois que a reação acabar. Os quadros cobrem um pouco antes e depois do \
candidato justamente para isso; se precisar de mais contexto que isso, pode ir além dos quadros.
4. Título: escreva pelo que se VÊ e pelo que é dito, seguindo as regras de título abaixo — 5 opções em \
`title_options` e a melhor em `title`.
5. Dê notas 0 a 100 em `scores`: hook (prende nos 3 primeiros segundos), flow (ritmo, sem partes mortas), \
value (graça/surpresa/valor), trend = COMPARTILHÁVEL (alguém mandaria para um amigo? É o sinal que mais \
leva o vídeo a quem não segue). `score` = sua nota geral. Em `reason`, diga em uma frase QUAIS sinais sustentam \
o corte (ex.: "gol sofrido 2x3 + reação z=6 + 'não acredito'") — o streamer vê isso para decidir rápido.
6. Edição: `peak` = segundo do acontecimento; `reactions` = até 2 segundos em que a CARA do streamer reage \
mais forte nos quadros (o vídeo dá um zoom ali — deixe vazio se não houver câmera ou reação visível); \
`keywords` = 1 a 4 palavras realmente ditas no trecho que carregam a graça (serão destacadas na legenda); \
`post` = descrição curta para postar, com tom de zoeira, no idioma do vídeo: uma linha que amplia a \
curiosidade sem repetir o título (sem "kkkk"), terminando com 3 a 5 hashtags (jogo + nicho).
Mantenha o número do candidato em `candidate`. Revise todos os candidatos."""


def system_prompt(content_type: str, style: dict | None) -> str:
    text = CONTENT.get(content_type, CONTENT["game"]) + "\n" + OUTPUT_RULES
    if style and style.get("guide"):
        text += f"\n\nGuia de estilo a seguir (\"{style['name']}\"):\n{style['guide']}"
    return text


def format_transcript(transcript: dict, events: list[dict], start: float = 0.0, end: float = float("inf")) -> str:
    lines: list[tuple[float, str]] = []
    for seg in transcript["segments"]:
        if seg["e"] < start or seg["s"] > end:
            continue
        lines.append((seg["s"], f"[{seg['s']:.2f} → {seg['e']:.2f}] {seg['text']}"))
    for ev in events:
        if start <= ev["t"] <= end:
            lines.append((ev["t"], signals.describe(ev)))
    lines.sort(key=lambda x: x[0])
    return "\n".join(text for _, text in lines)


def build_prompt(transcript: dict, events: list[dict], duration: float, opts: dict) -> str:
    extra = (opts.get("instructions") or "").strip()
    start = float(opts.get("range_start") or 0)
    end = float(opts.get("range_end") or 0) or duration
    want = int(opts["num_clips"])
    review = opts.get("visual_review", True)
    count = min(20, want * 2) if review else want
    parts = [
        f"Duração total do vídeo: {duration:.1f}s." + (f" Analise só o trecho de {start:.0f}s a {end:.0f}s." if start or end < duration else ""),
        f"Quantidade: até {count} {'candidatos (serão revisados depois vendo os frames)' if review else 'clipes'}.",
        f"Duração de cada clipe: entre {opts['min_len']}s e {opts['max_len']}s.",
    ]
    if extra:
        parts.append(f"Instruções extras do usuário: {extra}")
    if any(e["kind"] == "marcador" and start <= e["t"] <= end for e in events):
        parts.append("Os 📌 marcadores foram postos pelo próprio streamer durante a live: cada um deve virar um "
                     "candidato (o momento marcado, não o segundo do atalho), salvo se não houver nada ali.")
    parts += ["", "<transcricao>", format_transcript(transcript, events, start, end), "</transcricao>", "",
              'Responda somente com JSON {"clips": [{"title", "start", "end", "peak", "hook", "reason", "score", "hashtags"}]}.']
    return "\n".join(parts)


def manual_prompt(system: str, prompt: str) -> str:
    return system + "\n\n---\n\n" + prompt


def review_prompt(candidates: list[Clip], transcript: dict, events: list[dict], windows: list[tuple[float, float]],
                  images: list[str], opts: dict) -> str:
    parts = []
    extra = (opts.get("instructions") or "").strip()
    if extra:
        parts.append(f"Instruções do usuário (prioridade máxima): {extra}\n")
    parts.append(f"Duração desejada: {opts['min_len']}s a {opts['max_len']}s. Fique com no máximo "
                 f"{opts['num_clips']} clipes com keep=true (os melhores).\n")
    for i, (c, (ws, we), img) in enumerate(zip(candidates, windows, images), 1):
        parts.append(f"## Candidato {i} — imagem {img} (quadros de {ws:.1f}s a {we:.1f}s)")
        parts.append(f"Proposta: \"{c.title}\" de {c.start:.2f}s a {c.end:.2f}s, pico em {c.peak or c.start:.2f}s. {c.reason}")
        parts.append(format_transcript(transcript, events, ws, we))
        parts.append("")
    parts.append('Responda somente com JSON {"clips": [...]} com um item por candidato.')
    return "\n".join(parts)


# ------------------------------------------------------------------ refazer um corte com instrução

REDO_SYSTEM = """Você é o editor ajustando UM corte de live a pedido do streamer. Você recebe a instrução dele, \
os limites atuais do corte, a fala e os eventos em volta (gols lidos no placar, reações) e uma folha de quadros \
com o segundo do vídeo escrito em cada quadro. Devolva os novos limites em segundos do vídeo original, seguindo a \
instrução ao pé da letra e mantendo as regras de bom corte (sem cortar palavra, contexto antes do lance, fim logo \
depois da reação). `peak` = segundo do acontecimento principal. Mude o título só se a instrução pedir ou se o \
corte mudar de assunto; senão repita o atual. `resposta` = uma frase dizendo o que você mudou."""

REDO_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["start", "end", "peak", "title", "resposta"],
    "properties": {
        "start": {"type": "number"}, "end": {"type": "number"}, "peak": {"type": "number"},
        "title": {"type": "string"}, "resposta": {"type": "string"},
    },
}


# ------------------------------------------------------------------ teste do estranho

STRANGER_SYSTEM = """Você nunca viu esta live nem conhece o streamer. Para cada clipe você recebe só uma folha de \
quadros (o segundo do vídeo está em cada quadro) e o que é falado nele — sem título nem explicação. Responda \
com honestidade: dá para entender o que aconteceu e por que é interessante nos primeiros 3 segundos, sem \
contexto da live? Em `motivo`, uma frase: o que falta (ex.: "não dá para saber que ele estava ganhando")."""

STRANGER_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["clipes"],
    "properties": {"clipes": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["n", "entende", "motivo"],
        "properties": {"n": {"type": "integer"}, "entende": {"type": "boolean"}, "motivo": {"type": "string"}},
    }}},
}
STRANGER_CAP = 60  # quem não passa no teste fica no máximo com essa nota


def stranger_prompt(items: list[tuple[int, str, str]]) -> str:
    """items = (n, nome da imagem, fala do trecho)."""
    parts = []
    for n, img, speech in items:
        parts.append(f"## Clipe {n} — imagem {img}\nFala: {speech or '(sem fala)'}\n")
    parts.append('Responda somente com JSON {"clipes": [{"n", "entende", "motivo"}]} com um item por clipe.')
    return "\n".join(parts)


# ------------------------------------------------------------------ backends

def find_cli() -> str | None:
    explicit = os.environ.get("CLAUDE_CLI")
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("claude")
    if found:
        # No Windows o npm instala um atalho .cmd; chamar o .exe real evita o cmd.exe
        # mexer em aspas e quebras de linha dos argumentos.
        if Path(found).suffix.lower() in (".cmd", ".ps1", ""):
            exe = Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
            if exe.exists():
                return str(exe)
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = glob.glob(os.path.join(local, "Claude-Data", "*", "claude-code", "*", "*", "claude.exe"))
    if candidates:
        return max(candidates, key=os.path.getmtime)
    return None


def cli_status() -> dict:
    path = find_cli()
    if not path:
        return {"available": False, "logged_in": False, "path": None}
    try:
        proc = subprocess.run([path, "auth", "status"], capture_output=True, text=True, timeout=20)
        logged = bool(json.loads(proc.stdout).get("loggedIn"))
    except Exception:
        logged = False
    return {"available": True, "logged_in": logged, "path": path}


def api_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def resolve_backend(requested: str) -> str:
    if requested in ("api", "cli", "manual"):
        return requested
    if api_available():
        return "api"
    if cli_status()["logged_in"]:
        return "cli"
    return "manual"


USAGE_FILE = None  # definido por quem usa (jobs.py): planilha de consumo por chamada


def _log_usage(step: str, backend: str, model: str, effort: str, images: int, usage: dict, cost: float | None,
               turns: int | None, seconds: float) -> None:
    """Uma linha por chamada ao Claude: base para otimizar tokens com números, não com palpite."""
    if USAGE_FILE is None:
        return
    import csv
    import time as _time

    try:
        USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        new = not USAGE_FILE.exists()
        with open(USAGE_FILE, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            if new:
                w.writerow(["quando", "etapa", "backend", "modelo", "esforco", "imagens", "rodadas", "entrada",
                            "cache_criado", "cache_lido", "saida", "custo_usd", "segundos"])
            w.writerow([_time.strftime("%Y-%m-%d %H:%M:%S"), step, backend, model, effort, images, turns or "",
                        usage.get("input_tokens", 0), usage.get("cache_creation_input_tokens", 0),
                        usage.get("cache_read_input_tokens", 0), usage.get("output_tokens", 0),
                        round(cost, 5) if cost is not None else "", round(seconds, 1)])
    except OSError:
        pass  # registro é diagnóstico: nunca derruba um corte


def ask(backend: str, system: str, prompt: str, schema: dict, images: list[Path] | None = None,
        step: str = "", model: str | None = None, effort: str | None = None) -> dict:
    """Uma chamada estruturada ao Claude. `step` nomeia a etapa no registro de consumo; `model`/`effort`
    permitem usar um modelo/esforço menor em tarefas mecânicas (ler placar, calibrar)."""
    model, effort = model or MODEL, effort or EFFORT
    if backend == "cli":
        return _ask_cli(system, prompt, schema, images or [], step, model, effort)
    if backend == "api":
        return _ask_api(system, prompt, schema, images or [], step, model, effort)
    raise ValueError(f"backend sem chamada automática: {backend}")


def _ask_cli(system: str, prompt: str, schema: dict, images: list[Path], step: str = "",
             model: str = MODEL, effort: str = EFFORT) -> dict:
    path = find_cli()
    if not path:
        raise RuntimeError("Claude Code CLI não encontrado. Instale com `npm i -g @anthropic-ai/claude-code` "
                           "e rode `claude auth login`.")
    # pasta temporária: nada de CLAUDE.md de outro projeto, e as imagens ficam acessíveis ao Read
    with tempfile.TemporaryDirectory(prefix="cortai-") as tmp:
        system_file = Path(tmp) / "system.txt"
        system_file.write_text(system, encoding="utf-8")
        # sem os servidores MCP do usuário: eles somavam ~123 mil tokens de definição de ferramentas em TODA
        # chamada (medido: 126.840 → 3.947 tokens numa chamada mínima)
        mcp_file = Path(tmp) / "mcp.json"
        mcp_file.write_text('{"mcpServers": {}}', encoding="utf-8")
        for img in images:
            shutil.copy(img, Path(tmp) / img.name)
        if images:
            names = ", ".join(img.name for img in images)
            prompt = (f"Antes de responder, abra com a ferramenta Read TODAS estas imagens (estão na pasta atual): "
                      f"{names}.\n\n{prompt}")
        cmd = [
            path, "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
            "--system-prompt-file", str(system_file),
            "--model", model,
            "--effort", effort,
            "--no-session-persistence",
            "--strict-mcp-config", "--mcp-config", str(mcp_file),
        ]
        cmd += ["--tools", "Read", "--allowedTools", "Read"] if images else ["--tools", ""]
        import time as _time
        t0 = _time.time()
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              cwd=tmp, timeout=1800)
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"resposta inesperada do Claude Code: {(proc.stderr or proc.stdout)[-800:]}")
    _log_usage(step, "cli", model, effort, len(images), result.get("usage") or {}, result.get("total_cost_usd"),
               result.get("num_turns"), _time.time() - t0)
    if result.get("is_error"):
        raise RuntimeError(f"Claude Code: {result.get('result')}")
    if isinstance(result.get("structured_output"), dict):
        return result["structured_output"]
    return extract_json(result.get("result", ""))


def _ask_api(system: str, prompt: str, schema: dict, images: list[Path], step: str = "",
             model: str = MODEL, effort: str = EFFORT) -> dict:
    import anthropic

    content: list[dict] = []
    for img in images:
        content.append({"type": "text", "text": f"Imagem {img.name}:"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.b64encode(img.read_bytes()).decode()}})
    content.append({"type": "text", "text": prompt})
    import time as _time
    t0 = _time.time()
    client = anthropic.Anthropic()
    # Fallback do lado do servidor: se o modelo recusar, a própria API reroteia para outro modelo.
    with client.beta.messages.stream(
        model=model,
        max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},
        system=system,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": content}],
    ) as stream:
        message = stream.get_final_message()
    u = message.usage
    _log_usage(step, "api", model, effort, len(images),
               {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
                "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}, None, 1, _time.time() - t0)
    if message.stop_reason == "refusal":
        raise RuntimeError("o Claude recusou analisar este conteúdo")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("resposta do Claude cortada por max_tokens")
    text = next((b.text for b in message.content if b.type == "text"), "")
    return json.loads(text)


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("nenhum JSON encontrado na resposta")
    return json.loads(text[start : end + 1])


def parse_plan(data: dict | str) -> list[Clip]:
    if isinstance(data, str):
        data = extract_json(data)
    try:
        return ClipPlan.model_validate(data).clips
    except ValidationError as exc:
        raise ValueError(f"JSON de clipes inválido: {exc}") from exc


# --------------------------------------------------------------- pós-processamento

ANCHOR_SNAP = 4.0       # s: o pico do Claude encaixa no gol/reação mais perto dentro dessa distância
GOAL_REACTION = 12.0    # s depois do gol: a reação cabe aqui; o resto é o replay automático do jogo
PRE_ROLL_MIN = 3.0      # s mínimos de contexto antes do pico


def _snap_anchor(clip: "Clip", anchors: list[dict]) -> tuple[float | None, dict | None]:
    """O Claude erra o segundo exato (a pesquisa mostra LLMs ruins em localizar tempo); o placar e o
    volume não. Gol tem prioridade sobre reação."""
    if clip.peak is None or not anchors:
        return clip.peak, None
    near = [a for a in anchors if abs(a["t"] - clip.peak) <= ANCHOR_SNAP]
    if not near:
        return clip.peak, None
    best = min(near, key=lambda a: (a["kind"] == "reacao", abs(a["t"] - clip.peak)))
    return float(best["t"]), best


def snap_clips(clips: list[Clip], words: list[dict], duration: float, min_len: float, max_len: float,
               anchors: list[dict] | None = None) -> list[dict]:
    """Alinha início/fim a limites de palavra, encaixa o pico na âncora (gol/reação), corta o replay
    automático depois de um gol, aplica limites de duração e remove sobreposição."""
    out: list[dict] = []
    for clip in sorted((c for c in clips if c.keep), key=lambda c: c.score, reverse=True):
        start, end = max(0.0, clip.start), min(duration, clip.end)
        if end <= start:
            continue
        peak, anchor = _snap_anchor(clip, anchors or [])
        if peak is not None and start <= peak <= end:
            if peak - start < PRE_ROLL_MIN:
                start = max(0.0, peak - 8.0)  # sem contexto antes do lance quem chega não entende
            goals_in = [a for a in (anchors or []) if a["kind"] in ("gol_nosso", "gol_deles") and start <= a["t"] <= end]
            later = [a for a in (anchors or []) if a["kind"] != "reacao" and goals_in and a["t"] > goals_in[-1]["t"] and a["t"] <= end]
            if goals_in and not later and end > goals_in[-1]["t"] + GOAL_REACTION + 2:
                end = goals_in[-1]["t"] + GOAL_REACTION  # o encaixe na palavra abaixo termina a frase
        if words:
            # só encaixa no começo de uma palavra se ela estiver perto: em corte de jogo o início
            # pode ser de propósito num momento sem fala
            near = [w for w in words if abs(w["s"] - start) <= 0.6]
            if near:
                start = max(0.0, min(near, key=lambda w: abs(w["s"] - start))["s"] - 0.12)
            cut = [w for w in words if start < w["s"] < end < w["e"]]  # palavra cortada no fim
            if cut:
                end = cut[0]["e"]
        if end - start > max_len:
            end = start + max_len
            if words:
                fit = [w for w in words if w["s"] >= start and w["e"] <= end]
                if fit and end - fit[-1]["e"] < 1.5:
                    end = fit[-1]["e"]
        end = min(duration, end + 0.2)
        if end - start < min_len * 0.6:
            continue
        if any(start < o["end"] and end > o["start"] for o in out):
            continue
        picked = titles.choose(clip.title_options, clip.title)
        out.append({
            "candidate": clip.candidate,
            **picked,
            "start": round(start, 2),
            "end": round(end, 2),
            "hook": clip.hook.strip(),
            "reason": clip.reason.strip(),
            "visual": clip.visual.strip(),
            "score": max(0, min(100, int(clip.score))),
            "scores": {k: max(0, min(100, int(v))) for k, v in clip.scores.items()},
            "peak": peak,
            "anchor": {k: anchor[k] for k in ("kind", "t", "placar") if k in anchor} if anchor else None,
            "reactions": [float(r) for r in clip.reactions if start <= r <= end][:2],
            "keywords": [k.strip() for k in clip.keywords if k.strip()][:4],
            "post": clip.post.strip(),
            "hashtags": [h if h.startswith("#") else f"#{h}" for h in (t.strip() for t in clip.hashtags) if h],
        })
    return out
