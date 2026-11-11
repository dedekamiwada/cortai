"""Pós-edição: pega um clipe JÁ PRONTO (do Cortaí ou de fora, até com legenda queimada) e devolve
uma versão com mais drama — zooms, tremor, flash de texto, selos, emotes, SFX e trilha.

Fluxo de um projeto (pasta `_posedit/<id>/`):
  1. análise: transcreve o clipe, mede picos/silêncios e monta folhas de frames (1 por segundo);
  2. plano: o Claude devolve os beats (`SCHEMA`); o código valida contra a biblioteca e a fala;
  3. revisão: o usuário liga/desliga beats na tabela;
  4. render: ffmpeg aplica os beats, trata a voz e exporta COM e SEM trilha (-14 LUFS).

A biblioteca (`_biblioteca/` ao lado dos projetos, ou a pasta escolhida) tem `sfx/`, `emotes/` e
`musicas/<clima>/`; o usuário coloca ali só arquivos que tem direito de usar. Sem música de um clima,
`tensao` e `triste` usam uma trilha original gerada (`sintese.py`).
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import frames, jobs, media, paths, picker, render, signals, sintese, transcribe

ROOT = paths.JOBS_DIR / "_posedit"
CONFIG = ROOT / "config.json"
W, H = 1080, 1920
CLIMAS = ("tensao", "corte_seco", "triste", "epico", "comico")
TIPOS = ("zoom_punch", "zoom_lento", "tremor", "flash_texto", "selo", "emote", "sfx")
AUDIO_EXT = {".wav", ".mp3", ".ogg", ".m4a", ".flac", ".aac", ".opus"}
IMAGE_EXT = {".png", ".webp", ".jpg", ".jpeg"}
SHEET_SECONDS = 12  # cada folha de frames cobre 12 s, 1 quadro por segundo
SFX_VOLUME = 0.5    # ~-6 dB
LOUDNESS, TRUE_PEAK = -14, -2.0  # o AAC sobe o pico ~0,5 dB; sai ≤ -1,5 dBFS
VOICE_KEY = 0.04    # limiar do ducking (voz como chave)

_projects: dict[str, dict] = {}
_lock = threading.RLock()
_worker = ThreadPoolExecutor(max_workers=1)


# ------------------------------------------------------------------ biblioteca

def library_dir() -> Path:
    try:
        chosen = json.loads(CONFIG.read_text(encoding="utf-8")).get("biblioteca")
    except (FileNotFoundError, json.JSONDecodeError):
        chosen = None
    return Path(chosen) if chosen else paths.JOBS_DIR / "_biblioteca"


def set_library_dir(folder: str) -> dict:
    p = Path(folder.strip().strip('"')) if folder.strip() else None
    if p is not None and not p.is_dir():
        raise FileNotFoundError(f"pasta não encontrada: {p}")
    ROOT.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps({"biblioteca": str(p) if p else None}, ensure_ascii=False), encoding="utf-8")
    return library()


def _files(folder: Path, exts: set[str]) -> list[str]:
    if not folder.is_dir():
        return []
    return sorted(f.name for f in folder.iterdir() if f.is_file() and f.suffix.lower() in exts)


def library() -> dict:
    """Arquivos disponíveis. Cria as pastas e o kit básico de SFX original na primeira vez."""
    root = library_dir()
    for sub in ("sfx", "emotes", *(f"musicas/{c}" for c in CLIMAS if c != "corte_seco")):
        (root / sub).mkdir(parents=True, exist_ok=True)
    sintese.ensure_default_sfx(root / "sfx")
    return {
        "dir": str(root),
        "sfx": _files(root / "sfx", AUDIO_EXT),
        "emotes": _files(root / "emotes", IMAGE_EXT),
        "musicas": {c: _files(root / "musicas" / c, AUDIO_EXT) for c in CLIMAS if c != "corte_seco"},
        "geradas": list(sintese.GENERATED),
    }


# ------------------------------------------------------------------ persistência

def _dir(pid: str) -> Path:
    return ROOT / pid


def _save(p: dict) -> None:
    tmp = _dir(p["id"]) / "pos.json.tmp"
    tmp.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_dir(p["id"]) / "pos.json")


def update(pid: str, **fields) -> dict:
    with _lock:
        p = _projects[pid]
        p.update(fields)
        p["updated"] = time.time()
        _save(p)
        return p


def get(pid: str) -> dict | None:
    with _lock:
        p = _projects.get(pid)
        return json.loads(json.dumps(p)) if p else None


def list_all() -> list[dict]:
    with _lock:
        return sorted((dict(p) for p in _projects.values()), key=lambda p: p["created"], reverse=True)


def busy() -> bool:
    with _lock:
        return any(p["status"] not in ("ready", "done", "error") for p in _projects.values())


def delete(pid: str) -> None:
    with _lock:
        p = _projects.get(pid)
        if p and p["status"] not in ("ready", "done", "error"):
            raise ValueError("espere terminar o que está em andamento")
        _projects.pop(pid, None)
    shutil.rmtree(_dir(pid), ignore_errors=True)


def load_existing() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for f in ROOT.glob("*/pos.json"):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if p.get("status") not in ("ready", "done", "error"):
            p["status"], p["error"] = "error", "servidor reiniciado durante o processamento"
        _projects[p["id"]] = p


def _guard(pid: str, fn, *args) -> None:
    try:
        fn(pid, *args)
    except Exception as exc:
        traceback.print_exc()
        update(pid, status="error", error=str(exc), message="Erro")


# ------------------------------------------------------------------ criação e análise

def create(local_path: str = "", job_id: str = "", n: int | None = None, instructions: str = "") -> dict:
    if job_id:
        job = jobs.get(job_id)
        clip = next((c for c in (job or {}).get("clips", []) if c["n"] == n), None)
        if not clip or clip.get("status") != "done":
            raise ValueError("o clipe precisa estar renderizado")
        src = paths.JOBS_DIR / clip["file"]
        name = clip["title"]
    else:
        src = Path(local_path.strip().strip('"'))
        name = src.stem
    if not src.is_file():
        raise FileNotFoundError(f"arquivo não encontrado: {src}")
    pid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    _dir(pid).mkdir(parents=True)
    p = {
        "id": pid, "name": name, "created": time.time(), "updated": time.time(),
        "status": "queued", "progress": 0.0, "message": "Na fila", "error": None,
        "source": {"path": str(src), "job": job_id or None, "n": n},
        "instructions": instructions.strip(), "plan": None, "result": None, "rev": 0,
    }
    with _lock:
        _projects[pid] = p
        _save(p)
    _worker.submit(_guard, pid, _analyze)
    return p


def replan(pid: str, instructions: str) -> None:
    p = get(pid)
    if not p or p["status"] not in ("ready", "done", "error"):
        raise ValueError("espere terminar o que está em andamento")
    update(pid, instructions=instructions.strip(), status="queued", message="Na fila", error=None)
    _worker.submit(_guard, pid, _analyze)


def _words(pid: str) -> list[dict]:
    return transcribe.all_words(json.loads((_dir(pid) / "transcript.json").read_text(encoding="utf-8")))


def _analyze(pid: str) -> None:
    p = get(pid)
    d, src = _dir(pid), Path(p["source"]["path"])
    meta = media.probe(src)
    update(pid, meta=meta)
    opts = jobs.defaults()
    tfile, lfile = d / "transcript.json", d / "loudness.json"
    if not (tfile.exists() and lfile.exists()):
        audio = d / "audio.wav"
        update(pid, status="transcribing", message="Transcrevendo a fala…", progress=0.0)
        if meta["has_audio"]:
            media.extract_audio(src, audio, meta["duration"])
            transcript = transcribe.transcribe(audio, opts["whisper_model"], opts["language"], meta["duration"],
                                               on_progress=lambda x: update(pid, progress=round(x, 2)))
            loud = signals.loudness(audio)
            audio.unlink(missing_ok=True)
        else:
            transcript, loud = {"segments": []}, []
        tfile.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
        lfile.write_text(json.dumps(loud), encoding="utf-8")
    words = _words(pid)
    loud = json.loads(lfile.read_text(encoding="utf-8"))
    events = signals.audio_events(loud, words)

    update(pid, status="watching", message="Montando as folhas de frames…", progress=0.0)
    dur = meta["duration"]
    sheets = []
    for i in range(math.ceil(dur / SHEET_SECONDS)):
        a, b = i * SHEET_SECONDS, min(dur, (i + 1) * SHEET_SECONDS)
        if b - a < 0.5:
            break
        out = d / f"frames-{i + 1}.jpg"
        frames.grid(src, a, b, out, meta["width"], meta["height"], n=max(2, round(b - a)), cols=6)
        sheets.append(out)

    backend = picker.resolve_backend(opts.get("backend", "auto"))
    if backend == "manual":
        raise RuntimeError("a pós-edição precisa do Claude Code logado ou de ANTHROPIC_API_KEY")
    lib = library()
    update(pid, status="planning", message="Claude assistindo e montando os beats…")
    raw = picker.ask(backend, SYSTEM, build_prompt(dur, words, events, lib, p.get("instructions", "")),
                     SCHEMA, images=sheets)
    (d / "plan-raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    plan = normalize_plan(raw, dur, words, lib)
    update(pid, status="ready", message="Plano pronto: revise os beats e renderize", plan=plan,
           sheets=[f"_posedit/{pid}/{s.name}" for s in sheets])


# ------------------------------------------------------------------ plano do Claude

SYSTEM = """Você é o editor de pós-produção de cortes verticais de live (TikTok/Reels/Shorts) de um \
streamer brasileiro. Você recebe um clipe JÁ CORTADO e PRONTO — ele pode ter legenda e título gravados \
na imagem; NÃO adicione legenda. Seu trabalho é planejar a pós-edição que aumenta o drama e a graça, \
como um editor humano faria: zoom na hora certa, efeito sonoro no impacto, trilha que acompanha a jogada.

Antes de planejar, entenda a história: SETUP (o que cria a expectativa), PUNCHLINE (o acontecimento que \
quebra a expectativa) e REAÇÃO do streamer. Efeito só amplifica um momento que existe.

Tipos de beat (t = segundo do clipe em que começa; foco_x/foco_y = ponto no quadro de 0 a 1, \
esquerda→direita e topo→base, olhando as folhas de frames):
- zoom_punch: zoom seco no ROSTO na reação forte. dur 0,6–1,0. forca = zoom 1,3–1,5. foco = o rosto.
- zoom_lento: aproximação lenta em tensão crescente ou silêncio dramático (mão no rosto, choque). \
dur 2–5. forca 1,1–1,4. foco no que importa (rosto ou jogo).
- tremor: impacto, morte, susto, explosão. dur 0,3–0,5. forca = deslocamento em px (15–35).
- flash_texto: o efeito grande — fundo desfocado + texto gigante + flash branco. NO MÁXIMO 1 por clipe, \
na maior reação, logo DEPOIS da fala da punchline (nunca por cima dela). texto 1–3 palavras em caixa \
alta, específico do momento. dur ~1,1. Sem imagem do streamer.
- selo: adesivo vermelho com 1–2 palavras. Bom para ironia dramática (o público vê o perigo antes do \
streamer, ex.: "LAVA" apontando a lava) ou para rotular algo. dur 1–2. foco = onde fica o selo.
- emote: imagem de reação da lista de emotes (use o nome EXATO do arquivo em `arquivo`). Reação curta no \
fim de um beat. dur 1,5–3. foco = onde fica.
- sfx: efeito sonoro da lista (nome EXATO em `arquivo`). Coloque no mesmo t do beat visual que ele \
acompanha. NUNCA por cima da fala: escolha t numa pausa entre palavras.

Trilha: seções {de, ate, clima}. Climas: tensao (antes do acontecimento, crescendo), corte_seco (a \
música PARA de repente no acontecimento — seção curta, sem som), triste (depois do fracasso), epico \
(jogada boa), comico (situação boba). Em `arquivo` use um nome da lista daquele clima ou "" para a \
trilha gerada (só existe para tensao e triste; sem arquivo nos outros climas a seção fica muda). A \
trilha abaixa sozinha quando o streamer fala.

Regras:
1. Um efeito visual por momento. Se tudo treme, nada treme. Típico: 4–8 beats em 30 s.
2. Preserve a pausa antes da punchline: nenhum efeito nela.
3. selo e emote não podem cobrir o rosto, a ação principal nem a faixa de legenda gravada (veja onde a \
legenda aparece nas folhas e use foco_y fora dela).
4. fim = segundo em que o vídeo deve acabar: no máximo ~1,5 s depois da última reação. Se o clipe já \
termina bem, use a duração inteira. Nunca corte no meio de uma frase.
5. `o_que` = o que acontece na tela/na fala naquele segundo (curto, para a tabela de revisão). \
`motivo` = por que esse efeito ali (curto).
6. Textos em PT-BR, sem "kkkk", sem emoji, sem explicar a piada.
7. Campos que não se aplicam ao tipo: texto "", arquivo "", foco 0,5, forca 0.

As imagens são folhas de frames do clipe com o segundo escrito em cada quadro."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["historia", "fim", "beats", "trilha"],
    "properties": {
        "historia": {"type": "string", "description": "Setup, punchline e reação em 1–3 frases"},
        "fim": {"type": "number", "description": "Segundo em que o vídeo deve acabar"},
        "beats": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["t", "tipo", "dur", "o_que", "motivo", "texto", "arquivo", "foco_x", "foco_y", "forca"],
            "properties": {
                "t": {"type": "number"},
                "tipo": {"type": "string", "enum": list(TIPOS)},
                "dur": {"type": "number"},
                "o_que": {"type": "string"},
                "motivo": {"type": "string"},
                "texto": {"type": "string"},
                "arquivo": {"type": "string"},
                "foco_x": {"type": "number"},
                "foco_y": {"type": "number"},
                "forca": {"type": "number"},
            },
        }},
        "trilha": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["de", "ate", "clima", "arquivo"],
            "properties": {
                "de": {"type": "number"},
                "ate": {"type": "number"},
                "clima": {"type": "string", "enum": list(CLIMAS)},
                "arquivo": {"type": "string"},
            },
        }},
    },
}


def build_prompt(dur: float, words: list[dict], events: list[dict], lib: dict, instructions: str) -> str:
    fala = " ".join(f"{w['w'].strip()}@{w['s']:.2f}" for w in words) or "(sem fala)"
    sinais = "\n".join(signals.describe(e) for e in events) or "(nenhum)"
    musicas = "\n".join(f"- {c}: {', '.join(fs) if fs else '(vazio' + (' → trilha gerada' if c in lib['geradas'] else ' → mudo') + ')'}"
                        for c, fs in lib["musicas"].items())
    extra = f"\n\nPedido do streamer para esta edição: {instructions}" if instructions else ""
    return f"""Duração do clipe: {dur:.2f} s.

Fala (cada palavra com o segundo em que começa):
{fala}

Sinais de áudio (picos e silêncios):
{sinais}

Biblioteca disponível:
- sfx: {', '.join(lib['sfx']) or '(vazio)'}
- emotes: {', '.join(lib['emotes']) or '(vazio — não use emote)'}
- músicas por clima:
{musicas}{extra}

Monte o plano de pós-edição."""


DEFAULT_DUR = {"zoom_punch": 0.8, "zoom_lento": 3.0, "tremor": 0.4, "flash_texto": 1.1, "selo": 1.4,
               "emote": 2.5, "sfx": 0.0}
FORCE = {"zoom_punch": (1.4, 1.15, 1.7), "zoom_lento": (1.25, 1.05, 1.5), "tremor": (24, 8, 50)}


def _clean_text(text: str, limit: int) -> str:
    text = " ".join((text or "").replace("{", "").replace("}", "").replace("\\", "").split())
    return text.upper()[:limit]


def normalize_plan(raw: dict, dur: float, words: list[dict], lib: dict) -> dict:
    """Valida o plano do Claude: tempos dentro do clipe, arquivos que existem, 1 flash no máximo, e
    SFX por cima da fala chega desligado (o usuário decide)."""
    fim = float(raw.get("fim") or dur)
    fim = round(min(dur, max(min(3.0, dur), fim)), 2)
    beats, flash = [], False
    for b in sorted(raw.get("beats") or [], key=lambda x: float(x.get("t", 0))):
        tipo = b.get("tipo")
        if tipo not in TIPOS:
            continue
        t = round(min(max(float(b.get("t", 0)), 0.0), fim - 0.05), 2)
        d = float(b.get("dur") or 0) or DEFAULT_DUR[tipo]
        beat = {"tipo": tipo, "t": t, "dur": round(max(0.1, min(d, fim - t)), 2) if tipo != "sfx" else 0.0,
                "o_que": (b.get("o_que") or "").strip(), "motivo": (b.get("motivo") or "").strip(),
                "texto": "", "arquivo": "", "on": True, "aviso": "",
                "foco_x": round(min(max(float(b.get("foco_x", 0.5)), 0.0), 1.0), 3),
                "foco_y": round(min(max(float(b.get("foco_y", 0.5)), 0.0), 1.0), 3)}
        if tipo in FORCE:
            default, lo, hi = FORCE[tipo]
            f = float(b.get("forca") or 0)
            beat["forca"] = round(min(hi, max(lo, f)) if f else default, 2)
        if tipo == "flash_texto":
            beat["texto"] = _clean_text(b.get("texto", ""), 24)
            if flash or not beat["texto"]:
                continue
            flash = True
        elif tipo == "selo":
            beat["texto"] = _clean_text(b.get("texto", ""), 14)
            if not beat["texto"]:
                continue
        elif tipo in ("emote", "sfx"):
            name = (b.get("arquivo") or "").strip()
            if name not in lib["emotes" if tipo == "emote" else "sfx"]:
                continue
            beat["arquivo"] = name
        if tipo == "sfx" and any(w["s"] - 0.05 < t < w["e"] - 0.05 for w in words):
            beat["on"], beat["aviso"] = False, "cai por cima da fala"
        beats.append(beat)
    for i, beat in enumerate(beats):
        beat["id"] = i

    trilha = []
    for s in sorted(raw.get("trilha") or [], key=lambda x: float(x.get("de", 0))):
        clima = s.get("clima")
        de = round(min(max(float(s.get("de", 0)), 0.0), fim), 2)
        ate = round(min(max(float(s.get("ate", 0)), de), fim + 2.5), 2)  # pode passar do fim: o fade cobre
        if clima not in CLIMAS or ate - de < 0.2:
            continue
        name = (s.get("arquivo") or "").strip()
        if clima == "corte_seco" or name not in lib["musicas"].get(clima, []):
            name = ""
        fonte = name or ("gerada" if clima in lib["geradas"] else ("" if clima == "corte_seco" else "mudo"))
        trilha.append({"id": len(trilha), "de": de, "ate": min(ate, fim), "clima": clima, "arquivo": name,
                       "fonte": fonte, "on": True})
    return {"historia": (raw.get("historia") or "").strip(), "fim": fim, "beats": beats, "trilha": trilha}


# ------------------------------------------------------------------ render

def start_render(pid: str, off: list[int], off_trilha: list[int], textos: dict) -> None:
    p = get(pid)
    if not p or p["status"] not in ("ready", "done", "error") or not p.get("plan"):
        raise ValueError("o plano ainda não está pronto")
    plan = p["plan"]
    for b in plan["beats"]:
        b["on"] = b["id"] not in off
        new = textos.get(str(b["id"]))
        if new is not None and b["tipo"] in ("flash_texto", "selo"):
            b["texto"] = _clean_text(new, 24 if b["tipo"] == "flash_texto" else 14) or b["texto"]
    for s in plan["trilha"]:
        s["on"] = s["id"] not in off_trilha
    update(pid, plan=plan, status="rendering", progress=0.0, message="Renderizando…", error=None)
    _worker.submit(_guard, pid, _render)


def _smooth(a: float, up: float, b: float, down: float) -> str:
    """Envelope 0→1→0: sobe de a em `up` s, desce até b em `down` s, com curva suave (cosseno)."""
    e = f"min(clip((t-{a:.3f})/{up:.3f},0,1),clip(({b:.3f}-t)/{down:.3f},0,1))"
    return f"(0.5-0.5*cos(PI*{e}))"


def _motion_filter(beats: list[dict]) -> str | None:
    """Zoom (punch e lento) e tremor numa passada: scale por quadro + crop mirando o foco."""
    zooms, shakes = [], []
    for b in beats:
        if b["tipo"] == "zoom_punch":
            zooms.append((_smooth(b["t"], 0.08, b["t"] + b["dur"] + 0.15, 0.15), b))
        elif b["tipo"] == "zoom_lento":
            up = max(0.5, b["dur"] - 0.4)
            zooms.append((_smooth(b["t"], up, b["t"] + b["dur"] + 0.8, 0.8), b))
        elif b["tipo"] == "tremor":
            shakes.append(b)
    if not zooms and not shakes:
        return None
    factors = [f"(1+{b['forca'] - 1:.3f}*{env})" for env, b in zooms]
    dx, dy = [], []
    for b in shakes:
        a, d, f = b["t"], b["dur"], b["forca"]
        on = f"between(t,{a:.3f},{a + d:.3f})"
        decay = f"(1-(t-{a:.3f})/{d:.3f})"
        factors.append(f"(1+0.07*{on})")
        dy.append(f"{on}*{f:.1f}*{decay}*sin(2*PI*14*t)")
        dx.append(f"{on}*{f * 0.4:.1f}*{decay}*cos(2*PI*9*t)")
    z = "*".join(factors)
    if zooms:
        # foco = média dos focos dos zooms ativos, pesada pelo envelope (0,5 quando nenhum está ativo)
        wsum = "+".join(env for env, _ in zooms)
        sx = "+".join("%.3f*%s" % (b["foco_x"], env) for env, b in zooms)
        sy = "+".join("%.3f*%s" % (b["foco_y"], env) for env, b in zooms)
        fx = f"(({sx})+0.0005)/({wsum}+0.001)"
        fy = f"(({sy})+0.0005)/({wsum}+0.001)"
    else:
        fx = fy = "0.5"
    x = f"{fx}*(iw-{W})" + "".join(f"+{e}" for e in dx)
    y = f"{fy}*(ih-{H})" + "".join(f"+{e}" for e in dy)
    return (f"scale=w='2*trunc({W // 2}*{z})':h='2*trunc({H // 2}*{z})':eval=frame:flags=bicubic,"
            f"crop={W}:{H}:x='clip({x},0,iw-{W})':y='clip({y},0,ih-{H})'")


def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Flash,Arial Black,150,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,2,0,1,11,0,5,60,60,0,1
Style: Selo,Arial Black,92,&H00FFFFFF,&H000000FF,&H001A0EE1,&H001A0EE1,-1,0,0,0,100,100,2,0,3,22,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass(beats: list[dict]) -> str | None:
    lines = []
    for b in beats:
        a, e = _ass_time(b["t"]), _ass_time(b["t"] + b["dur"])
        if b["tipo"] == "flash_texto":
            # entra pequeno e "estoura" (pop), como o flash meme do Remotion
            text = b["texto"].replace(" ", "\\N", 1) if len(b["texto"]) > 11 else b["texto"]
            lines.append(f"Dialogue: 2,{a},{e},Flash,,0,0,0,,{{\\an5\\pos(540,960)\\fscx35\\fscy35"
                         f"\\t(0,150,\\fscx108\\fscy108)\\t(150,260,\\fscx100\\fscy100)}}{text}")
        elif b["tipo"] == "selo":
            x, y = int(80 + b["foco_x"] * (W - 160)), int(160 + b["foco_y"] * (H - 320))
            lines.append(f"Dialogue: 1,{a},{e},Selo,,0,0,0,,{{\\an5\\pos({x},{y})\\frz-7\\fscx30\\fscy30"
                         f"\\t(0,130,\\fscx110\\fscy110)\\t(130,220,\\fscx100\\fscy100)}}{b['texto']}")
    return ASS_HEADER + "\n".join(lines) + "\n" if lines else None


def _base_chain(meta: dict) -> str:
    """Leva qualquer vídeo para 1080x1920: vertical é só escalado; o resto entra inteiro com fundo desfocado."""
    if 0.54 <= meta["width"] / meta["height"] <= 0.585:
        return f"[0:v]scale={W}:{H}:flags=lanczos,setsar=1[b0]"
    return (f"[0:v]split=2[pa][pb];[pa]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"gblur=sigma=30,eq=brightness=-0.08[bg];[pb]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[b0]")


def _render_video(pid: str, plan: dict, meta: dict, out: Path) -> None:
    d = _dir(pid)
    fim = plan["fim"]
    beats = [b for b in plan["beats"] if b["on"]]
    lib_dir = library_dir()
    graph = [_base_chain(meta)]
    cur = "b0"
    motion = _motion_filter(beats)
    if motion:
        graph.append(f"[{cur}]{motion}[mv]")
        cur = "mv"
    for i, b in enumerate(b for b in beats if b["tipo"] == "flash_texto"):
        on = f"between(t,{b['t']:.3f},{b['t'] + b['dur']:.3f})"
        graph.append(f"[{cur}]gblur=sigma=16:enable='{on}',"
                     f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.35:t=fill:enable='{on}'[fl{i}]")
        cur = f"fl{i}"
    inputs: list[str] = []
    emotes = [b for b in beats if b["tipo"] == "emote"]
    for i, b in enumerate(emotes):
        inputs += ["-loop", "1", "-t", f"{fim:.3f}", "-i", str(lib_dir / "emotes" / b["arquivo"])]
        size = 300
        x = int(min(max(b["foco_x"] * W - size / 2, 30), W - size - 30))
        y = int(min(max(b["foco_y"] * H - size / 2, 120), H - size - 120))
        graph.append(f"[{i + 1}:v]scale={size}:{size}:force_original_aspect_ratio=decrease,format=rgba,"
                     f"rotate=8*PI/180:c=none:ow=rotw(8*PI/180):oh=roth(8*PI/180),"
                     f"fade=t=in:st={b['t']:.3f}:d=0.12:alpha=1[em{i}]")
        graph.append(f"[{cur}][em{i}]overlay=x={x}:y={y}:enable='between(t,{b['t']:.3f},{b['t'] + b['dur']:.3f})'"
                     f":eof_action=pass[eo{i}]")
        cur = f"eo{i}"
    ass = _ass(beats)
    tail = ""
    if ass:
        (d / "pos.ass").write_text(ass, encoding="utf-8")
        tail = ",subtitles=pos.ass"
    flashes = []
    for b in beats:
        if b["tipo"] == "flash_texto":
            t0 = b["t"]
            # flash branco de ~0,27 s: sobe, estoura e some
            for a, e, alpha in ((0, 0.07, 0.45), (0.07, 0.17, 0.85), (0.17, 0.27, 0.35)):
                flashes.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=white@{alpha}:t=fill:"
                               f"enable='between(t,{t0 + a:.3f},{t0 + e:.3f})'")
    graph.append(f"[{cur}]null{tail}" + "".join("," + f for f in flashes) + "[v]")
    fps = min(meta.get("fps") or 30, 60)
    base = ["ffmpeg", "-y", "-v", "error", "-t", f"{fim:.3f}", "-i", str(Path(_projects[pid]["source"]["path"])),
            *inputs, "-filter_complex", ";".join(graph), "-map", "[v]", "-an"]
    render._run_encode(base, ["-r", f"{fps:.3f}", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out.name], d)


def _music_file(pid: str, s: dict) -> Path | None:
    if s["arquivo"]:
        return library_dir() / "musicas" / s["clima"] / s["arquivo"]
    audio = sintese.music(s["clima"], s["ate"] - s["de"] + 0.6)
    if audio is None:
        return None
    return sintese.write_wav(_dir(pid) / f"musica-{s['id']}.wav", audio)


def _render_audio(pid: str, plan: dict, meta: dict, sem: Path, com: Path) -> bool:
    """Mixa voz tratada + SFX (sem.wav) e, por cima, a trilha com ducking (com.wav).
    Devolve se havia alguma música para a versão com trilha."""
    fim = plan["fim"]
    inputs = ["-i", str(Path(_projects[pid]["source"]["path"]))]
    graph = []
    voice = render.VOICE_CHAIN if jobs.defaults().get("voice_enhance", True) else "anull"
    if meta.get("has_audio"):
        graph.append(f"[0:a:0]atrim=0:{fim:.3f},asetpts=PTS-STARTPTS,{voice},aresample=48000,"
                     f"aformat=channel_layouts=stereo,apad=whole_dur={fim:.3f}[voz]")
    else:
        graph.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{fim:.3f}[voz]")
    sfx = [b for b in plan["beats"] if b["on"] and b["tipo"] == "sfx"]
    music = [(s, f) for s in plan["trilha"] if s["on"] and s["clima"] != "corte_seco"
             for f in [_music_file(pid, s)] if f]
    n_in = 1
    sfx_labels = []
    for i, b in enumerate(sfx):
        inputs += ["-i", str(library_dir() / "sfx" / b["arquivo"])]
        ms = int(b["t"] * 1000)
        graph.append(f"[{n_in}:a]aresample=48000,aformat=channel_layouts=stereo,volume={SFX_VOLUME},"
                     f"adelay={ms}|{ms},asplit=2[sa{i}][sb{i}]")
        sfx_labels.append(i)
        n_in += 1
    graph.append("[voz]asplit=3[v1][v2][vk]")
    sem_in = "[v1]" + "".join(f"[sa{i}]" for i in sfx_labels)
    graph.append(f"{sem_in}amix=inputs={1 + len(sfx_labels)}:normalize=0:duration=first[sem]")
    if music:
        mus_labels = []
        for j, (s, f) in enumerate(music):
            inputs += ["-i", str(f)]
            length = s["ate"] - s["de"]
            ms = int(s["de"] * 1000)
            graph.append(f"[{n_in}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{length:.3f},"
                         f"asetpts=PTS-STARTPTS,afade=t=in:d=0.25,afade=t=out:st={max(0.0, length - 0.4):.3f}:d=0.4,"
                         f"loudnorm=I=-20:TP=-3:LRA=11,aresample=48000,adelay={ms}|{ms}[m{j}]")
            mus_labels.append(f"[m{j}]")
            n_in += 1
        graph.append(f"{''.join(mus_labels)}amix=inputs={len(mus_labels)}:normalize=0:duration=longest,"
                     f"apad=whole_dur={fim:.3f}[mus]")
        graph.append(f"[mus][vk]sidechaincompress=threshold={VOICE_KEY}:ratio=6:attack=15:release=450[mduck]")
        com_in = "[v2]" + "".join(f"[sb{i}]" for i in sfx_labels) + "[mduck]"
        graph.append(f"{com_in}amix=inputs={2 + len(sfx_labels)}:normalize=0:duration=first[com]")
    else:
        graph.append("[vk]anullsink")
        com_in = "[v2]" + "".join(f"[sb{i}]" for i in sfx_labels)
        graph.append(f"{com_in}amix=inputs={1 + len(sfx_labels)}:normalize=0:duration=first[com]")
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(graph),
           "-map", "[sem]", "-t", f"{fim:.3f}", "-c:a", "pcm_s16le", str(sem),
           "-map", "[com]", "-t", f"{fim:.3f}", "-c:a", "pcm_s16le", str(com)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou na mixagem: {proc.stderr[-1500:]}")
    return bool(music)


def _master(wav: Path) -> str:
    """Filtro final: limiter + loudnorm linear em 2 passadas (-14 LUFS, pico com folga para o AAC)."""
    pre = "alimiter=limit=0.8:attack=3:release=80:level=disabled"
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav), "-af",
                           f"{pre},loudnorm=I={LOUDNESS}:TP={TRUE_PEAK}:LRA=11:print_format=json", "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = proc.stderr
    try:
        m = json.loads(text[text.rindex("{"):text.rindex("}") + 1])
        if float(m["input_i"]) < -60:
            return pre
        return (f"{pre},loudnorm=I={LOUDNESS}:TP={TRUE_PEAK}:LRA=11:measured_I={m['input_i']}:"
                f"measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
                f"offset={m['target_offset']}:linear=true,aresample=48000")
    except (ValueError, KeyError):
        return pre


def measure(path: Path) -> dict:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true",
                           "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = {}
    for line in proc.stderr.splitlines()[::-1]:
        line = line.strip()
        if line.startswith("I:") and "lufs" not in out:
            out["lufs"] = float(line.split()[1])
        elif line.startswith("Peak:") and "peak" not in out:
            out["peak"] = float(line.split()[1])
    return out


def _render(pid: str) -> None:
    p = get(pid)
    d, plan, meta = _dir(pid), p["plan"], p["meta"]
    update(pid, message="Aplicando zooms, textos e efeitos no vídeo…", progress=0.1)
    _render_video(pid, plan, meta, d / "video.mp4")
    update(pid, message="Mixando voz, efeitos sonoros e trilha…", progress=0.6)
    has_music = _render_audio(pid, plan, meta, d / "sem.wav", d / "com.wav")
    update(pid, message="Ajustando o volume final…", progress=0.8)
    result = {"has_music": has_music}
    for key, wav, name in (("sem", d / "sem.wav", "final-sem-trilha.mp4"), ("com", d / "com.wav", "final-com-trilha.mp4")):
        if key == "com" and not has_music:
            continue
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", "video.mp4", "-i", wav.name, "-map", "0:v", "-map", "1:a",
               "-c:v", "copy", "-af", _master(wav), "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
               "-shortest", "-movflags", "+faststart", name]
        proc = subprocess.run(cmd, cwd=d, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg falhou ao juntar áudio e vídeo: {proc.stderr[-1500:]}")
        result[key] = {"file": f"_posedit/{pid}/{name}", **measure(d / name)}
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", "video.mp4", "-frames:v", "1",
                    "-vf", "scale=360:-2", "thumb.jpg"], cwd=d, capture_output=True)
    for tmp in ("video.mp4", "sem.wav", "com.wav"):
        (d / tmp).unlink(missing_ok=True)
    for f in d.glob("musica-*.wav"):
        f.unlink(missing_ok=True)
    result["thumb"] = f"_posedit/{pid}/thumb.jpg"
    update(pid, status="done", progress=1.0, result=result, rev=p.get("rev", 0) + 1,
           message="Pronto" + ("" if has_music else " (sem música na biblioteca para estes climas: só a versão sem trilha)"))


def open_library_folder() -> None:
    folder = library()["dir"]
    if hasattr(os, "startfile"):
        os.startfile(folder)  # noqa: S606 — pasta local escolhida pelo próprio usuário
