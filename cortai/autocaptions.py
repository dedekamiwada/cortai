"""Legenda automática no Estúdio: transcreve o áudio da faixa principal (Whisper, no PC) e devolve
textos de legenda no tempo da timeline, com o destaque palavra por palavra do Cortaí.

Roda em segundo plano; o andamento fica em project["captions_job"]. Os textos prontos ficam em
captions_job["items"] e o editor coloca na timeline (assim dá para desfazer e não briga com o
salvamento automático do editor)."""
from __future__ import annotations

import subprocess
import threading
import time
import traceback
from pathlib import Path

from . import captions, jobs, studio, to_studio, transcribe

_lock = threading.Lock()


def _set(pid: str, **fields) -> None:
    studio.mutate(pid, lambda p: p.__setitem__("captions_job", {**(p.get("captions_job") or {}), **fields}))


def start(pid: str, opts: dict) -> None:
    p = studio.load(pid)
    if (p.get("captions_job") or {}).get("status") == "running":
        raise ValueError("as legendas já estão sendo geradas")
    _set(pid, status="running", progress=0.0, message="Preparando o áudio…", error=None, items=None,
         started=time.time(), id=f"c{int(time.time())}")
    threading.Thread(target=_run, args=(pid, opts), daemon=True).start()


def _run(pid: str, opts: dict) -> None:
    try:
        with _lock:  # um Whisper por vez (memória)
            items = _transcribe_project(pid, opts)
        _set(pid, status="done", progress=1.0, items=items, finished=time.time(),
             message=f"{len(items)} legenda(s) prontas" if items else "Não encontrei fala no áudio")
    except Exception as exc:
        traceback.print_exc()
        _set(pid, status="error", error=str(exc), message="Erro")


def _transcribe_project(pid: str, opts: dict) -> list[dict]:
    p = studio.load(pid)
    layout = studio.main_layout(p)
    todo = []
    for it, start, end in layout:
        a = p["assets"].get(it.get("asset")) or {}
        if a.get("kind") in ("video", "audio") and a.get("has_audio") and not it.get("mute"):
            todo.append((it, start, end, a))
    if not todo:
        raise ValueError("nenhum vídeo da faixa principal tem som para transcrever")
    work = studio._pdir(pid) / "exports"
    work.mkdir(exist_ok=True)
    defaults = jobs.defaults()
    model = defaults.get("whisper_model") or "large-v3-turbo"  # o mesmo modelo escolhido para os cortes
    language = (opts.get("language") or defaults.get("language") or "pt").strip() or None
    total_len = sum(float(it["out"]) - float(it["in"]) for it, *_ in todo) or 1.0
    done_len = 0.0
    words: list[dict] = []
    for n, (it, start, end, a) in enumerate(todo, 1):
        src_in, src_out = float(it["in"]), float(it["out"])
        length = max(0.1, src_out - src_in)
        speed = max(0.1, float(it.get("speed") or 1))
        wav = work / f"legenda-{it['id']}.wav"
        _set(pid, message=f"Lendo o áudio {n}/{len(todo)}…")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{src_in:.3f}", "-t", f"{length:.3f}", "-i", a["path"],
                        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], capture_output=True)
        if not wav.is_file():
            continue
        base = done_len

        def prog(x: float, base=base, length=length) -> None:
            _set(pid, progress=round(min(0.99, (base + x * length) / total_len), 3),
                 message=f"Transcrevendo {n}/{len(todo)} com o Whisper…")

        tr = transcribe.transcribe(wav, model, language, length, on_progress=prog,
                                   on_fallback=lambda r: _set(pid, message="Sem GPU: transcrevendo no processador…"))
        wav.unlink(missing_ok=True)
        for w in transcribe.all_words(tr):
            s, e = start + float(w["s"]) / speed, start + float(w["e"]) / speed
            if e > start and s < end:
                words.append({"w": w["w"], "s": round(max(start, s), 3), "e": round(min(end, e), 3)})
        done_len += length
    words.sort(key=lambda w: w["s"])
    total = layout[-1][2]
    style = {
        "caption_preset": opts.get("preset") or "destaque",
        "caption_position": opts.get("position") or "bottom",
        "caption_color": opts.get("color") or "#FFE600",
        "caption_upper": bool(opts.get("upper", True)),
        "keyword_highlight": False,
    }
    if style["caption_preset"] not in captions.PRESETS:
        style["caption_preset"] = "destaque"
    canvas_h = p["canvas"]["h"]
    items = to_studio.texts_from_words(words, style, total, prefix="tauto")
    if canvas_h != to_studio.H:  # projeto em outro formato: mantém a posição relativa
        for it in items:
            it["size"] = round(it["size"] * canvas_h / to_studio.H)
    return items
