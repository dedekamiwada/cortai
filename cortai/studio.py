"""Estúdio: editor de vídeo completo, sem IA, 100% local.

Um projeto é um JSON com a tela (canvas), as mídias importadas (assets) e quatro faixas:
  main    — sequência magnética de vídeos/imagens, com transições entre eles
  overlay — picture-in-picture (vídeo/imagem por cima, com posição, escala e opacidade)
  text    — textos com estilo e animação
  audio   — músicas/efeitos/narração posicionados no tempo

Mídias são usadas no lugar onde estão no seu PC (nada é copiado nem enviado). Para a prévia
no navegador o app gera um proxy leve, uma tira de miniaturas e a forma de onda.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import media, signals

ROOT: Path  # definido por init()
_lock = threading.RLock()
_worker = ThreadPoolExecutor(max_workers=2)  # proxies e exportações, separado dos jobs de IA

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}
CANVAS_PRESETS = {
    "9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350), "3:4": (1080, 1440),
}


def init(jobs_dir: Path) -> None:
    global ROOT
    ROOT = jobs_dir / "_studio"
    ROOT.mkdir(parents=True, exist_ok=True)
    for f in ROOT.glob("*/project.json"):  # tarefas interrompidas por reinício
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        dirty = False
        for a in p.get("assets", {}).values():
            if a.get("status") == "processing":
                a.update(status="error", error="servidor reiniciado durante o processamento")
                dirty = True
        if (p.get("export") or {}).get("status") == "running":
            p["export"].update(status="error", error="servidor reiniciado durante a exportação")
            dirty = True
        if (p.get("captions_job") or {}).get("status") == "running":
            p["captions_job"].update(status="error", error="servidor reiniciado durante a transcrição")
            dirty = True
        if dirty:
            f.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")


def _pdir(pid: str) -> Path:
    d = ROOT / Path(pid).name
    if not d.is_dir():
        raise KeyError(pid)
    return d


def load(pid: str) -> dict:
    with _lock:
        return json.loads((_pdir(pid) / "project.json").read_text(encoding="utf-8"))


def _write(p: dict) -> None:
    f = _pdir(p["id"]) / "project.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)


def mutate(pid: str, fn) -> dict:
    """Altera o projeto sob trava (o editor e as tarefas em segundo plano escrevem no mesmo arquivo)."""
    with _lock:
        p = load(pid)
        fn(p)
        p["updated"] = time.time()
        _write(p)
        return p


def busy() -> bool:
    """Há exportação ou preparo de mídia em andamento em algum projeto."""
    for f in ROOT.glob("*/project.json"):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (p.get("export") or {}).get("status") == "running":
            return True
        if (p.get("captions_job") or {}).get("status") == "running":
            return True
        if any(a.get("status") == "processing" for a in p["assets"].values()):
            return True
    return False


def list_projects() -> list[dict]:
    out = []
    for f in ROOT.glob("*/project.json"):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        thumb = next((a.get("poster") for a in p["assets"].values() if a.get("poster")), None)
        out.append({"id": p["id"], "name": p["name"], "updated": p["updated"], "canvas": p["canvas"],
                    "duration": timeline_duration(p), "thumb": thumb})
    return sorted(out, key=lambda x: x["updated"], reverse=True)


def create(name: str, ratio: str = "9:16") -> dict:
    w, h = CANVAS_PRESETS.get(ratio, CANVAS_PRESETS["9:16"])
    pid = "p-" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    (ROOT / pid).mkdir(parents=True)
    p = {
        "id": pid, "name": name.strip() or "Projeto sem título", "created": time.time(), "updated": time.time(),
        "canvas": {"w": w, "h": h, "ratio": ratio, "fps": 30, "bg": "#000000"},
        "assets": {}, "tracks": {"main": [], "overlay": [], "text": [], "audio": []},
        "export": None,
    }
    _write(p)
    return p


def delete(pid: str) -> None:
    shutil.rmtree(_pdir(pid), ignore_errors=True)


# ------------------------------------------------------------------ salvar (vindo do editor)

_ITEM_KEYS = {
    "main": {"id", "asset", "in", "out", "speed", "volume", "mute", "fit", "zoom", "pan_x", "pan_y",
             "brightness", "contrast", "saturation", "transition", "fade_in", "fade_out", "reverse_hint",
             "crop_l", "crop_r", "crop_t", "crop_b", "flip_h", "flip_v", "rotate", "look", "look_amt", "fx",
             "voice_clean", "voice_fx", "kf", "kf_ease"},
    "overlay": {"id", "asset", "start", "in", "out", "x", "y", "scale", "opacity", "volume", "mute",
                "brightness", "contrast", "saturation", "crop_l", "crop_r", "crop_t", "crop_b", "flip_h", "flip_v",
                "rotate", "look", "look_amt", "fx", "voice_clean", "voice_fx", "key", "key_color", "key_sim",
                "key_blend", "blend", "kf", "kf_ease"},
    "text": {"id", "start", "dur", "text", "font", "size", "color", "stroke", "stroke_w", "bg", "x", "y",
             "anim", "bold", "italic", "align", "words", "hl", "hl_mode", "hl_pop", "kw_idx", "kw_color",
             "shadow", "shadow_d", "shadow_a", "kf", "kf_ease", "scale", "opacity"},
    "audio": {"id", "asset", "start", "in", "out", "volume", "fade_in", "fade_out", "speed", "mute",
              "voice_clean", "voice_fx", "kf", "kf_ease"},
}


def save(pid: str, incoming: dict) -> dict:
    """Recebe o estado do editor. Só aceita campos conhecidos; mídias continuam sendo do servidor."""
    def apply(p: dict) -> None:
        if "name" in incoming:
            p["name"] = str(incoming["name"])[:120] or p["name"]
        canvas = incoming.get("canvas") or {}
        if canvas.get("ratio") in CANVAS_PRESETS:
            p["canvas"]["ratio"] = canvas["ratio"]
            p["canvas"]["w"], p["canvas"]["h"] = CANVAS_PRESETS[canvas["ratio"]]
        if canvas.get("fps") in (24, 25, 30, 50, 60):
            p["canvas"]["fps"] = canvas["fps"]
        if isinstance(canvas.get("bg"), str) and canvas["bg"].startswith("#"):
            p["canvas"]["bg"] = canvas["bg"][:7]
        tracks = incoming.get("tracks")
        if isinstance(tracks, dict):
            for name, keys in _ITEM_KEYS.items():
                items = tracks.get(name)
                if not isinstance(items, list):
                    continue
                clean = []
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    it = {k: v for k, v in it.items() if k in keys}
                    if "asset" in keys and it.get("asset") not in p["assets"]:
                        continue
                    clean.append(it)
                p["tracks"][name] = clean
    return mutate(pid, apply)


# ------------------------------------------------------------------ mídias

def add_asset(pid: str, path: str | Path) -> dict:
    path = Path(str(path).strip().strip('"'))
    if not path.is_file():
        raise FileNotFoundError(f"arquivo não encontrado: {path}")
    load(pid)  # valida o projeto
    ext = path.suffix.lower()
    kind = "image" if ext in IMAGE_EXT else "audio" if ext in AUDIO_EXT else "video"
    aid = "a" + uuid.uuid4().hex[:8]
    asset = {"id": aid, "name": path.name, "path": str(path), "kind": kind, "status": "processing",
             "message": "Lendo arquivo…", "error": None}
    mutate(pid, lambda p: p["assets"].__setitem__(aid, asset))
    _worker.submit(_prepare_asset, pid, aid)
    return asset


def _set_asset(pid: str, aid: str, **fields) -> None:
    mutate(pid, lambda p: p["assets"][aid].update(fields))


def _prepare_asset(pid: str, aid: str) -> None:
    try:
        _prepare(pid, aid)
    except Exception as exc:
        traceback.print_exc()
        _set_asset(pid, aid, status="error", error=str(exc), message="Erro")


def _prepare(pid: str, aid: str) -> None:
    asset = load(pid)["assets"][aid]
    src = Path(asset["path"])
    d = _pdir(pid) / "media" / aid
    d.mkdir(parents=True, exist_ok=True)
    rel = f"_studio/{pid}/media/{aid}"

    if asset["kind"] == "image":
        info = _probe_image(src)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-frames:v", "1",
                        "-vf", "scale='min(720,iw)':-2", str(d / "poster.jpg")], capture_output=True)
        _set_asset(pid, aid, status="ready", message="Pronto", duration=None, has_audio=False,
                   poster=f"{rel}/poster.jpg", preview=f"{rel}/poster.jpg", **info)
        return

    info = media.probe_any(src)
    _set_asset(pid, aid, message="Gerando prévia…", **info)
    if asset["kind"] == "video":
        _proxy(src, info, d / "proxy.mp4", lambda p: _set_asset(pid, aid, message=f"Gerando prévia… {int(p * 100)}%"))
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(1.0, info['duration'] / 2):.2f}", "-i", str(src),
                        "-frames:v", "1", "-vf", "scale=-2:240", str(d / "poster.jpg")], capture_output=True)
        _thumb_strip(d / "proxy.mp4", info["duration"], d / "strip.jpg")
    if info.get("has_audio"):
        _set_asset(pid, aid, message="Lendo o áudio…")
        wav = d / "audio.wav"
        media.extract_audio(src, wav)
        loud = signals.loudness(wav)
        (d / "wave.json").write_text(json.dumps(loud), encoding="utf-8")
        if asset["kind"] == "audio":
            wav.replace(d / "preview.wav")  # o navegador toca WAV direto
        else:
            wav.unlink(missing_ok=True)
    fields = {"status": "ready", "message": "Pronto",
              "wave": f"{rel}/wave.json" if info.get("has_audio") else None}
    if asset["kind"] == "video":
        fields.update(preview=f"{rel}/proxy.mp4", poster=f"{rel}/poster.jpg", strip=f"{rel}/strip.jpg",
                      strip_count=STRIP_TILES)
    else:
        fields.update(preview=f"{rel}/preview.wav")
    _set_asset(pid, aid, **fields)


def _probe_image(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    w, h = (int(x) for x in out.strip().split(",")[:2])
    return {"width": w, "height": h}


def _proxy(src: Path, info: dict, out: Path, on_progress) -> None:
    """Proxy leve para a prévia: ≤ 640 px, keyframe a cada 0,5 s (rolar a timeline fica liso)."""
    vertical = info["width"] < info["height"]
    scale = "scale=-2:'min(640,ih)'" if vertical else "scale='min(640,iw)':-2"
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", str(src),
           "-vf", f"{scale},fps=30", "-g", "15", "-pix_fmt", "yuv420p"]
    enc = [["-c:v", "h264_nvenc", "-preset", "p3", "-cq", "28"], ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"]]
    audio = ["-c:a", "aac", "-b:a", "96k", "-ac", "2"] if info.get("has_audio") else ["-an"]
    last = ""
    for e in enc:
        proc = subprocess.Popen(cmd + e + audio + ["-movflags", "+faststart", str(out)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        for line in proc.stdout:
            k, _, v = line.strip().partition("=")
            if k == "out_time_us" and v.isdigit() and info["duration"]:
                on_progress(min(1.0, int(v) / 1e6 / info["duration"]))
        last = proc.stderr.read()
        if proc.wait() == 0:
            return
    raise RuntimeError(f"não consegui gerar a prévia: {last[-600:]}")


STRIP_TILES = 60


def _thumb_strip(proxy: Path, duration: float, out: Path) -> None:
    """Tira horizontal com 60 miniaturas igualmente espaçadas (a timeline recorta a que precisa)."""
    rate = STRIP_TILES / max(duration, 0.1)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(proxy),
                    "-vf", f"fps={rate:.6f},scale=-2:64,tile={STRIP_TILES}x1:padding=0",
                    "-frames:v", "1", "-q:v", "5", str(out)], capture_output=True)


# ------------------------------------------------------------------ tempo

def item_len(it: dict) -> float:
    return max(0.05, (float(it["out"]) - float(it["in"])) / max(0.1, float(it.get("speed") or 1)))


def main_layout(p: dict) -> list[tuple[dict, float, float]]:
    """(item, início, fim) de cada item da faixa principal; transições sobrepõem o anterior."""
    out, t = [], 0.0
    for i, it in enumerate(p["tracks"]["main"]):
        trans = it.get("transition") or {}
        d = float(trans.get("dur") or 0) if i > 0 and trans.get("type", "none") != "none" else 0.0
        d = min(d, item_len(it) / 2, item_len(p["tracks"]["main"][i - 1]) / 2) if i > 0 else 0.0
        start = t - d
        end = start + item_len(it)
        out.append((it, start, end))
        t = end
    return out


def timeline_duration(p: dict) -> float:
    layout = main_layout(p)
    return round(layout[-1][2], 3) if layout else 0.0
