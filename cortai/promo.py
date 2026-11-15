"""Chamada para a live: vídeo curto (8–20 s) para postar DURANTE a live e puxar gente para ela.

Estrutura (da referência estudada: @last0f23, "100 níveis de parkour", 12 s): imagem forte do desafio em
movimento lento + roteiro em 3 batidas — gancho ("esse é o mapa dos 100 níveis…"), agora ("nesse momento eu
tô em live tentando zerar"), chamada ("se a bolinha estiver vermelha, clica aí") — com legenda uma palavra por
vez e o selo LIVE com o avatar no fim. Tudo por código (ffmpeg), sem tokens; voz é a sua (gravada ou enviada).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from . import captions, jobs, media, paths, posedit, render, transcribe

ROOT = paths.JOBS_DIR / "_promo"
W, H = 1080, 1920
WPS = 2.7            # palavras por segundo quando não há voz (ritmo da referência: ~2,8)
GAP = 0.35           # pausa entre as batidas do roteiro sem voz
HOLD = 1.2           # o selo LIVE fica na tela depois da última palavra
MAX_DUR = 30.0
BADGE_POS = {"centro": 0.42, "topo": 0.24, "baixo": 0.62}

DEFAULTS = {
    "roteiro": {"gancho": "", "agora": "", "chamada": "Se a bolinha estiver vermelha, clica aí e vem assistir."},
    "fundo": {"tipo": "", "job": "", "start": 0.0, "dur": 12.0, "arquivo": ""},
    "voz": {"arquivo": "", "palavras": []},
    "visual": {"caption_preset": "uma", "caption_position": "bottom", "caption_color": "#FFE600", "caption_upper": True,
               "zoom": True, "selo": True, "selo_texto": "LIVE", "selo_pos": "centro", "avatar": ""},
    "musica": {"arquivo": "", "volume": 0.25, "fundo": 0.35},  # fundo = volume do som do próprio vídeo de fundo
}

_projects: dict[str, dict] = {}
_lock = threading.RLock()
_worker = ThreadPoolExecutor(max_workers=1)


def _dir(pid: str) -> Path:
    return ROOT / pid


def _save(p: dict) -> None:
    tmp = _dir(p["id"]) / "promo.json.tmp"
    tmp.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_dir(p["id"]) / "promo.json")


def load_existing() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for f in ROOT.glob("*/promo.json"):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if p.get("status") == "rendering":
            p["status"], p["error"] = "error", "servidor reiniciado durante o render"
        _projects[p["id"]] = p


def get(pid: str) -> dict | None:
    with _lock:
        p = _projects.get(pid)
        return json.loads(json.dumps(p)) if p else None


def list_all() -> list[dict]:
    with _lock:
        return sorted((json.loads(json.dumps(p)) for p in _projects.values()), key=lambda p: -p["created"])


def _set(pid: str, **fields) -> dict:
    with _lock:
        p = _projects[pid]
        p.update(fields)
        p["updated"] = time.time()
        _save(p)
        return p


def create(name: str = "") -> dict:
    pid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    _dir(pid).mkdir(parents=True)
    p = {"id": pid, "name": (name or "Chamada para a live").strip()[:80], "created": time.time(), "status": "draft",
         "error": None, "result": None, "rev": 0, **json.loads(json.dumps(DEFAULTS))}
    with _lock:
        _projects[pid] = p
        _save(p)
    return get(pid)


def delete(pid: str) -> None:
    with _lock:
        _projects.pop(pid, None)
    shutil.rmtree(_dir(pid), ignore_errors=True)


def update(pid: str, body: dict) -> dict:
    """Guarda o que mudou no editor (cada parte validada; o resto fica como estava)."""
    p = get(pid)
    if not p:
        raise KeyError(pid)
    if p["status"] == "rendering":
        raise ValueError("espere o vídeo terminar")
    out = {}
    if "name" in body:
        out["name"] = str(body["name"]).strip()[:80] or p["name"]
    if isinstance(body.get("roteiro"), dict):
        out["roteiro"] = {k: str(body["roteiro"].get(k, p["roteiro"][k]) or "").strip()[:220] for k in DEFAULTS["roteiro"]}
    if isinstance(body.get("fundo"), dict):
        f = {**p["fundo"], **{k: body["fundo"][k] for k in ("tipo", "job", "start", "dur") if k in body["fundo"]}}
        if f["tipo"] not in ("", "projeto", "arquivo"):
            raise ValueError("fundo inválido")
        f["start"] = max(0.0, float(f["start"] or 0))
        f["dur"] = min(MAX_DUR, max(3.0, float(f["dur"] or 12)))
        if f["tipo"] == "projeto" and not jobs.get(f["job"]):
            raise ValueError("projeto não encontrado")
        out["fundo"] = f
    if isinstance(body.get("visual"), dict):
        v = {**p["visual"], **{k: body["visual"][k] for k in DEFAULTS["visual"] if k in body["visual"] and k != "avatar"}}
        if v["caption_preset"] not in captions.PRESETS:
            v["caption_preset"] = "uma"
        if v["caption_position"] not in captions.POSITIONS:
            v["caption_position"] = "bottom"
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(v["caption_color"])):
            v["caption_color"] = "#FFE600"
        v["selo_pos"] = v["selo_pos"] if v["selo_pos"] in BADGE_POS else "centro"
        v["selo_texto"] = str(v["selo_texto"] or "LIVE").strip()[:14]
        for k in ("caption_upper", "zoom", "selo"):
            v[k] = bool(v[k])
        out["visual"] = v
    if isinstance(body.get("musica"), dict):
        m = {**p["musica"], **body["musica"]}
        pasta, _, nome = str(m.get("arquivo") or "").partition("/")
        lib = posedit.library_dir() / "musicas"
        m["arquivo"] = f"{pasta}/{nome}" if nome and "/" not in nome and (lib / pasta / nome).is_file() else ""
        m["volume"] = min(1.0, max(0.0, float(m.get("volume") or 0)))
        m["fundo"] = min(1.0, max(0.0, float(m.get("fundo", 0.35) or 0)))
        out["musica"] = m
    if out:
        _set(pid, **out, status="draft" if p["status"] != "draft" else p["status"])
    return get(pid)


def attach(pid: str, kind: str, upload: Path) -> dict:
    """Arquivo enviado (fundo, avatar ou voz) vira parte da chamada. A voz é transcrita para a legenda seguir a fala."""
    p = get(pid)
    if not p:
        raise KeyError(pid)
    if kind not in ("fundo", "avatar", "voz"):
        raise ValueError("tipo de arquivo inválido")
    dest = _dir(pid) / f"{kind}{upload.suffix.lower() or '.bin'}"
    for old in _dir(pid).glob(f"{kind}.*"):
        old.unlink(missing_ok=True)
    shutil.move(str(upload), dest)
    if kind == "fundo":
        info = media.probe(dest)
        _set(pid, fundo={**p["fundo"], "tipo": "arquivo", "arquivo": dest.name, "start": 0.0,
                         "dur": min(MAX_DUR, info["duration"])}, preview=None)
    elif kind == "avatar":
        img = cv2.imdecode(np.fromfile(str(dest), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            dest.unlink(missing_ok=True)
            raise ValueError("não consegui ler a imagem")
        _set(pid, visual={**p["visual"], "avatar": dest.name})
    else:
        words = _transcribe(dest)
        if not words:
            raise ValueError("não ouvi fala nesse áudio")
        _set(pid, voz={"arquivo": dest.name, "palavras": words,
                       "duracao": round(media.probe_any(dest)["duration"], 2)})
    return get(pid)


def remove_voice(pid: str) -> dict:
    p = get(pid)
    if not p:
        raise KeyError(pid)
    for old in _dir(pid).glob("voz.*"):
        old.unlink(missing_ok=True)
    _set(pid, voz=json.loads(json.dumps(DEFAULTS["voz"])))
    return get(pid)


def _transcribe(audio: Path) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        wav = media.extract_audio(audio, Path(tmp) / "a.wav")
        tr = transcribe.transcribe(wav, "large-v3-turbo", "pt", media.probe_any(audio)["duration"])
    return [{"w": w["w"].strip(), "s": round(w["s"], 2), "e": round(w["e"], 2)} for w in transcribe.all_words(tr) if w["w"].strip()]


def _fold(w: str) -> str:
    import unicodedata
    w = unicodedata.normalize("NFD", w.lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in w if unicodedata.category(c) != "Mn"))


def timeline(p: dict) -> tuple[list[dict], float, float]:
    """(palavras com tempo, duração, quando começa a chamada — é aí que o selo LIVE entra).
    Com voz: as palavras faladas. Sem voz: o roteiro no ritmo de leitura."""
    call = [_fold(w) for w in p["roteiro"]["chamada"].split()][:2]
    if p["voz"].get("palavras"):
        words = p["voz"]["palavras"]
        dur = min(MAX_DUR, max(p["voz"].get("duracao") or 0, words[-1]["e"]) + HOLD)
        late = [w for w in words if w["s"] >= words[-1]["e"] * 0.4]  # a chamada está no fim da fala
        hit = next((w["s"] for w in late if call and _fold(w["w"]) == call[0]), None)
        return words, dur, hit if hit is not None else max(0.0, dur - HOLD - 2.5)
    words, t, call_t = [], 0.3, None
    for part in ("gancho", "agora", "chamada"):
        if part == "chamada":
            call_t = t
        for w in p["roteiro"][part].split():
            d = max(0.22, min(0.6, 0.18 + len(w) * 0.035)) * (2.7 / WPS)
            words.append({"w": w, "s": round(t, 2), "e": round(t + d, 2)})
            t += d
        if p["roteiro"][part].split():
            t += GAP
    dur = min(MAX_DUR, max(4.0, t + HOLD))
    return words, dur, call_t if call_t is not None and p["roteiro"]["chamada"].strip() else max(0.0, dur - HOLD - 2.5)


def _background(p: dict) -> tuple[Path, float, dict]:
    f = p["fundo"]
    if f["tipo"] == "arquivo" and f.get("arquivo"):
        src = _dir(p["id"]) / f["arquivo"]
        return src, f["start"], media.probe(src)
    if f["tipo"] == "projeto":
        job = jobs.get(f["job"])
        return Path(job["source"]["path"]), f["start"], job["source"]
    raise ValueError("escolha o vídeo de fundo")


def preview_bg(pid: str) -> dict:
    """Prévia leve (H.264) do fundo escolhido, para tocar no editor; refeita só quando o fundo muda."""
    p = get(pid)
    src, start, meta = _background(p)
    span = min(MAX_DUR, meta["duration"] - start) if p["fundo"]["tipo"] == "projeto" else p["fundo"]["dur"]
    key = f"{src.name}:{start:.2f}:{span:.2f}"
    out = _dir(pid) / "fundo_previa.mp4"
    if p.get("preview") != key or not out.exists():
        render.proxy(src, meta, start, start + span, out)
        _set(pid, preview=key)
    return {"file": f"_promo/{pid}/fundo_previa.mp4", "key": key}


def _badge_png(p: dict, out: Path) -> Path | None:
    """Avatar redondo com anel vermelho (o texto LIVE entra pela legenda ASS, com animação)."""
    name = p["visual"].get("avatar")
    if not name:
        return None
    img = cv2.imdecode(np.fromfile(str(_dir(p["id"]) / name), np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    size, ring = 360, 16
    h, w = img.shape[:2]
    side = min(h, w)
    img = img[(h - side) // 2:(h - side) // 2 + side, (w - side) // 2:(w - side) // 2 + side]
    img = cv2.resize(img, (size - 2 * ring, size - 2 * ring), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((size, size, 4), np.uint8)
    cv2.circle(canvas, (size // 2, size // 2), size // 2 - 1, (40, 30, 230, 255), -1, cv2.LINE_AA)  # anel (BGR vermelho)
    inner = np.zeros((size, size), np.uint8)
    cv2.circle(inner, (size // 2, size // 2), size // 2 - ring, 255, -1, cv2.LINE_AA)
    patch = np.zeros((size, size, 3), np.uint8)
    patch[ring:size - ring, ring:size - ring] = img
    alpha = inner.astype(np.float32)[..., None] / 255
    canvas[..., :3] = (patch * alpha + canvas[..., :3] * (1 - alpha)).astype(np.uint8)
    cv2.imencode(".png", canvas)[1].tofile(str(out))
    return out


def _ass(p: dict, words: list[dict], dur: float, badge_t: float) -> str:
    v = p["visual"]
    opts = {"caption_preset": v["caption_preset"], "caption_position": v["caption_position"],
            "caption_color": v["caption_color"], "caption_upper": v["caption_upper"], "captions": True,
            "keyword_highlight": False, "title_overlay": False}
    text = captions.build_ass(words, 0.0, dur, opts)
    if v["selo"]:
        y = int(H * BADGE_POS[v["selo_pos"]]) + (230 if v.get("avatar") else 0)
        a = captions._ts(badge_t)
        e = captions._ts(dur)
        label = captions._escape(v["selo_texto"].upper())
        # pílula vermelha com texto branco em itálico, entrando com um "pop"
        text += (f"Dialogue: 5,{a},{e},Default,,0,0,0,,{{\\an5\\pos(540,{y})\\fnArial Black\\fs120\\i1\\bord24\\shad0"
                 f"\\3c&H1A1AE6&\\1c&HFFFFFF&\\fscx30\\fscy30\\t(0,140,\\fscx112\\fscy112)\\t(140,240,\\fscx100\\fscy100)}}"
                 f"● {label}\n")
    return text


def start_render(pid: str) -> None:
    p = get(pid)
    if not p:
        raise KeyError(pid)
    if p["status"] == "rendering":
        raise ValueError("já está gerando")
    _background(p)  # erro claro antes de entrar na fila
    words, _, _ = timeline(p)
    if not words:
        raise ValueError("escreva o roteiro ou envie a sua voz")
    _set(pid, status="rendering", error=None)
    _worker.submit(_guard, pid)


def _guard(pid: str) -> None:
    try:
        _render(pid)
    except Exception as exc:
        traceback.print_exc()
        _set(pid, status="error", error=str(exc)[:300])


def _render(pid: str) -> None:
    p = get(pid)
    d = _dir(pid)
    words, dur, badge_t = timeline(p)
    src, start, meta = _background(p)
    (d / "chamada.ass").write_text(_ass(p, words, dur, badge_t), encoding="utf-8")
    badge = _badge_png(p, d / "selo.png") if p["visual"]["selo"] else None
    loop = ["-stream_loop", "-1"] if p["fundo"]["tipo"] == "arquivo" else []
    inputs = [*loop, "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
    cover = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1")
    zoom = (f",scale=w='2*trunc({W // 2}*(1+0.08*t/{dur:.2f}))':h='2*trunc({H // 2}*(1+0.08*t/{dur:.2f}))':eval=frame,"
            f"crop={W}:{H}") if p["visual"]["zoom"] else ""
    graph = [f"[0:v]{cover}{zoom},fps=30[bg]"]
    cur = "bg"
    n = 1
    if badge:
        inputs += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(badge)]
        y = int(H * BADGE_POS[p["visual"]["selo_pos"]]) - 180
        graph.append(f"[{n}:v]format=rgba,fade=t=in:st={badge_t:.2f}:d=0.15:alpha=1[sl]")
        graph.append(f"[{cur}][sl]overlay=x=(W-w)/2:y={y}:enable='gte(t,{badge_t:.2f})'[bs]")
        cur, n = "bs", n + 1
    graph.append(f"[{cur}]subtitles=chamada.ass[v]")
    # áudio: sua voz (tratada e nivelada) + música da biblioteca abaixando quando você fala
    voice = p["voz"].get("arquivo")
    music = p["musica"].get("arquivo")
    bg_vol = float(p["musica"].get("fundo", 0.35) or 0)
    audio_parts, labels = [], []
    if meta.get("has_audio") and bg_vol > 0:  # som do vídeo de fundo (jogo/live), baixo por baixo da voz
        audio_parts.append(f"[0:a]aresample=48000,aformat=channel_layouts=stereo,volume={bg_vol:.2f},"
                           f"apad=whole_dur={dur:.2f},atrim=0:{dur:.2f}[bga]")
    if voice:
        inputs += ["-i", str(d / voice)]
        audio_parts.append(f"[{n}:a]{render.voice_chain(True, 'medio')},aresample=48000,aformat=channel_layouts=stereo,"
                           f"apad=whole_dur={dur:.2f}[voz]")
        n += 1
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(posedit.library_dir() / "musicas" / music)]
        audio_parts.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,volume={p['musica']['volume']:.2f},"
                           f"atrim=0:{dur:.2f},afade=t=out:st={max(0, dur - 0.8):.2f}:d=0.8[mus]")
        n += 1
    beds = [x for x, on in (("mus", music), ("bga", meta.get("has_audio") and bg_vol > 0)) if on]
    if len(beds) == 2:
        audio_parts.append("[mus][bga]amix=inputs=2:duration=first:normalize=0[bed]")
        bed = "bed"
    else:
        bed = beds[0] if beds else None
    if voice and bed:  # música e som do jogo abaixam quando você fala
        audio_parts.append(f"[voz]asplit=2[vz][key];[{bed}][key]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=300[duck];"
                           "[vz][duck]amix=inputs=2:duration=first:normalize=0[mix]")
        alabel = "mix"
    elif voice or bed:
        alabel = "voz" if voice else bed
    else:
        audio_parts.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.2f}[sil]")
        alabel = "sil"
    audio_parts.append(f"[{alabel}]loudnorm=I=-14:TP=-2:LRA=11,aresample=48000[a]")
    out = d / "chamada.mp4"
    base = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(graph + audio_parts),
            "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}"]
    render._run_encode(base, ["-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out.name], d)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(dur - 0.5, badge_t + 0.4):.2f}", "-i", out.name,
                    "-frames:v", "1", "-vf", "scale=360:-2", "thumb.jpg"], cwd=d, capture_output=True)
    _set(pid, status="done", rev=p.get("rev", 0) + 1,
         result={"file": f"_promo/{pid}/chamada.mp4", "thumb": f"_promo/{pid}/thumb.jpg", "duracao": round(dur, 1),
                 "post": post_text(p)})


def post_text(p: dict) -> str:
    r = p["roteiro"]
    head = r["gancho"] or "AO VIVO AGORA"
    return f"{head} 🔴 Ao vivo agora: clica na minha foto e vem!\n\n#live #aovivo #minecraft #gamer"
