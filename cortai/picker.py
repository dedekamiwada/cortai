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
- 💥 som alto SEM fala: risada, grito, explosão/gol/morte no jogo — forte candidato a pico;
- 🎮 intervalo sem fala mas com som: reação, risada longa ou jogo acontecendo;
- 🤐 silêncio: choque, concentração ou tela parada;
- 🔊 grito/fala muito alta.
Frases curtas repetidas ("não não não", "corre corre", "ERREI") e xingamentos de surpresa também marcam pico.

Para cada corte:
- identifique o segundo do acontecimento principal (`peak`);
- comece 5 a 10 s antes do pico, direto no que monta a situação (sem introdução);
- termine quando a reação acabar — não corte no meio da risada;
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
value (graça/surpresa/valor), trend (potencial de compartilhamento). `score` = sua nota geral.
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


def ask(backend: str, system: str, prompt: str, schema: dict, images: list[Path] | None = None) -> dict:
    if backend == "cli":
        return _ask_cli(system, prompt, schema, images or [])
    if backend == "api":
        return _ask_api(system, prompt, schema, images or [])
    raise ValueError(f"backend sem chamada automática: {backend}")


def _ask_cli(system: str, prompt: str, schema: dict, images: list[Path]) -> dict:
    path = find_cli()
    if not path:
        raise RuntimeError("Claude Code CLI não encontrado. Instale com `npm i -g @anthropic-ai/claude-code` "
                           "e rode `claude auth login`.")
    # pasta temporária: nada de CLAUDE.md de outro projeto, e as imagens ficam acessíveis ao Read
    with tempfile.TemporaryDirectory(prefix="cortai-") as tmp:
        system_file = Path(tmp) / "system.txt"
        system_file.write_text(system, encoding="utf-8")
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
            "--model", MODEL,
            "--effort", EFFORT,
            "--no-session-persistence",
        ]
        cmd += ["--tools", "Read", "--allowedTools", "Read"] if images else ["--tools", ""]
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              cwd=tmp, timeout=1800)
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"resposta inesperada do Claude Code: {(proc.stderr or proc.stdout)[-800:]}")
    if result.get("is_error"):
        raise RuntimeError(f"Claude Code: {result.get('result')}")
    if isinstance(result.get("structured_output"), dict):
        return result["structured_output"]
    return extract_json(result.get("result", ""))


def _ask_api(system: str, prompt: str, schema: dict, images: list[Path]) -> dict:
    import anthropic

    content: list[dict] = []
    for img in images:
        content.append({"type": "text", "text": f"Imagem {img.name}:"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.b64encode(img.read_bytes()).decode()}})
    content.append({"type": "text", "text": prompt})
    client = anthropic.Anthropic()
    # Fallback do lado do servidor: se o modelo recusar, a própria API reroteia para outro modelo.
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},
        system=system,
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": content}],
    ) as stream:
        message = stream.get_final_message()
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

def snap_clips(clips: list[Clip], words: list[dict], duration: float, min_len: float, max_len: float) -> list[dict]:
    """Alinha início/fim a limites de palavra, aplica limites de duração e remove sobreposição."""
    out: list[dict] = []
    for clip in sorted((c for c in clips if c.keep), key=lambda c: c.score, reverse=True):
        start, end = max(0.0, clip.start), min(duration, clip.end)
        if end <= start:
            continue
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
            "peak": clip.peak,
            "reactions": [float(r) for r in clip.reactions if start <= r <= end][:2],
            "keywords": [k.strip() for k in clip.keywords if k.strip()][:4],
            "post": clip.post.strip(),
            "hashtags": [h if h.startswith("#") else f"#{h}" for h in (t.strip() for t in clip.hashtags) if h],
        })
    return out
