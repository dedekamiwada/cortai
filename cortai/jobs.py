"""Pipeline de um job: ingestão → transcrição → sinais de áudio → Claude propõe candidatos →
Claude revisa vendo os frames → renderização. Também cuida do editor de clipes e do
aprendizado de estilos."""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import frames, media, picker, render, signals, styles, transcribe

ROOT = Path(__file__).resolve().parent.parent
JOBS_DIR = Path(os.environ.get("CORTAI_JOBS_DIR") or ROOT / "jobs")
JOBS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_OPTS = {
    "num_clips": 6,
    "min_len": 15,
    "max_len": 45,
    "instructions": "",
    "content_type": "game",
    "style_id": styles.BUILTIN["id"],
    "visual_review": True,
    "range_start": 0,
    "range_end": 0,
    "backend": "auto",
    "whisper_model": "large-v3-turbo",
    "language": "",
    "layout": "auto",
    "captions": True,
    "caption_preset": "destaque",
    "caption_color": "#FFE600",
    "caption_upper": True,
    "caption_position": "bottom",
    "title_overlay": False,
}

# o que o editor pode mudar em um clipe (sobrepõe as opções do job)
CLIP_OVERRIDES = ("layout", "crop_x", "captions", "caption_preset", "caption_color", "caption_upper",
                  "caption_position", "title_overlay", "title_text", "word_fixes")

_jobs: dict[str, dict] = {}
_lock = threading.RLock()
_worker = ThreadPoolExecutor(max_workers=1)  # um job por vez: GPU e ffmpeg já paralelizam por dentro


# ------------------------------------------------------------------ persistência

def _dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _save(job: dict) -> None:
    tmp = _dir(job["id"]) / "job.json.tmp"
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_dir(job["id"]) / "job.json")


def update(job_id: str, **fields) -> dict:
    with _lock:
        job = _jobs[job_id]
        job.update(fields)
        job["updated"] = time.time()
        _save(job)
        return job


def load_existing() -> None:
    for f in JOBS_DIR.glob("*/job.json"):
        try:
            job = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if job.get("status") not in ("done", "error", "waiting_manual"):
            job["status"], job["error"] = "error", "servidor reiniciado durante o processamento"
        for clip in job.get("clips", []):
            if clip.get("status") in ("rendering", "pending"):
                clip["status"] = "error"
                clip["error"] = "servidor reiniciado durante a renderização"
        job["options"] = {**DEFAULT_OPTS, **job.get("options", {})}
        _jobs[job["id"]] = job
    for st in styles.list_styles(JOBS_DIR):
        if st.get("status") == "learning":
            st.update(status="error", error="servidor reiniciado durante o aprendizado")
            styles.save(JOBS_DIR, st)


def list_jobs() -> list[dict]:
    with _lock:
        return sorted((dict(j) for j in _jobs.values()), key=lambda j: j["created"], reverse=True)


def get(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return json.loads(json.dumps(job)) if job else None


def delete(job_id: str) -> None:
    with _lock:
        _jobs.pop(job_id, None)
    shutil.rmtree(_dir(job_id), ignore_errors=True)


# ------------------------------------------------------------------ criação

def create(name: str, opts: dict, url: str | None = None, local_path: str | None = None,
           upload_path: Path | None = None) -> dict:
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    d = _dir(job_id)
    d.mkdir(parents=True)
    source: dict = {}
    if upload_path is not None:
        dest = d / ("source" + upload_path.suffix.lower())
        shutil.move(str(upload_path), dest)
        source["path"] = str(dest)
    elif local_path:
        p = Path(local_path.strip().strip('"'))
        if not p.is_file():
            shutil.rmtree(d, ignore_errors=True)
            raise FileNotFoundError(f"arquivo não encontrado: {p}")
        source["path"] = str(p)  # referência ao arquivo original, sem copiar
    elif url:
        source["url"] = url.strip()
    else:
        shutil.rmtree(d, ignore_errors=True)
        raise ValueError("envie um arquivo, um caminho local ou uma URL")

    job = {
        "id": job_id, "name": name or source.get("url") or Path(source["path"]).name,
        "created": time.time(), "updated": time.time(),
        "status": "queued", "progress": 0.0, "message": "Na fila",
        "options": {**DEFAULT_OPTS, **opts}, "backend": None,
        "source": source, "clips": [], "error": None,
    }
    with _lock:
        _jobs[job_id] = job
        _save(job)
    _worker.submit(_guard, job_id, _run_pipeline)
    return job


def _guard(job_id: str, fn, *args) -> None:
    try:
        fn(job_id, *args)
    except Exception as exc:
        traceback.print_exc()
        update(job_id, status="error", error=str(exc), message="Erro")


# ------------------------------------------------------------------ pipeline

def _throttled_progress(job_id: str, p: float) -> None:
    """O ffmpeg reporta progresso várias vezes por segundo; grava no máximo a cada 1%."""
    if abs(p - _jobs[job_id].get("progress", 0.0)) >= 0.01 or p >= 1.0:
        update(job_id, progress=round(p, 3))


def _ensure_audio(job_id: str, path: Path, duration: float) -> Path:
    audio = _dir(job_id) / "audio.wav"
    if not audio.exists():
        update(job_id, status="transcribing", message="Extraindo áudio…", progress=0.0)
        tmp = audio.with_suffix(".tmp.wav")
        media.extract_audio(path, tmp, duration, on_progress=lambda p: _throttled_progress(job_id, p))
        tmp.replace(audio)
    return audio


def _transcript_and_signals(job_id: str) -> tuple[dict, list[float]]:
    job = get(job_id)
    d, src, opts = _dir(job_id), job["source"], job["options"]
    path = Path(src["path"])
    tfile, lfile = d / "transcript.json", d / "loudness.json"
    if tfile.exists() and lfile.exists():
        return (json.loads(tfile.read_text(encoding="utf-8")),
                json.loads(lfile.read_text(encoding="utf-8")))
    audio = _ensure_audio(job_id, path, src["duration"])
    if tfile.exists():
        transcript = json.loads(tfile.read_text(encoding="utf-8"))
    else:
        update(job_id, status="transcribing", progress=0.0,
               message=f"Transcrevendo com Whisper ({opts['whisper_model']})…")
        transcript = transcribe.transcribe(
            audio, opts["whisper_model"], opts["language"], src["duration"],
            on_progress=lambda p: _throttled_progress(job_id, p),
            on_fallback=lambda reason: update(
                job_id, progress=0.0,
                message=f"GPU indisponível ({reason[:120]}) — transcrevendo na CPU, bem mais lento…"),
        )
        tfile.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
    update(job_id, message="Medindo picos de áudio…")
    loud = signals.loudness(audio)
    lfile.write_text(json.dumps(loud), encoding="utf-8")
    audio.unlink(missing_ok=True)
    return transcript, loud


def _run_pipeline(job_id: str) -> None:
    job = get(job_id)
    d = _dir(job_id)
    opts = job["options"]

    src = job["source"]
    if "path" not in src:
        update(job_id, status="downloading", message="Baixando vídeo…", progress=0.0)
        src["path"] = str(media.download(src["url"], d)[0])
    path = Path(src["path"])
    src.update(media.probe(path))
    update(job_id, source=src)
    if not src["has_audio"]:
        raise RuntimeError("o vídeo não tem áudio — não dá para transcrever")

    transcript, loud = _transcript_and_signals(job_id)
    if not transcript["segments"]:
        raise RuntimeError("nenhuma fala detectada no vídeo")
    words = transcribe.all_words(transcript)
    events = signals.audio_events(loud, words)

    style = styles.get(JOBS_DIR, opts.get("style_id"))
    system = picker.system_prompt(opts["content_type"], style)
    prompt = picker.build_prompt(transcript, events, src["duration"], opts)
    (d / "prompt.txt").write_text(picker.manual_prompt(system, prompt), encoding="utf-8")

    backend = picker.resolve_backend(opts["backend"])
    update(job_id, backend=backend)
    if backend == "manual":
        update(job_id, status="waiting_manual", progress=0.0,
               message="Cole o prompt no Claude e traga a resposta de volta")
        return

    who = "Claude Code" if backend == "cli" else "API"
    update(job_id, status="selecting", progress=0.0,
           message=f"Claude ({who}) lendo a transcrição e os picos de áudio…")
    raw = picker.ask(backend, system, prompt, picker.CANDIDATES_SCHEMA)
    (d / "candidates.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    candidates = [c for c in picker.parse_plan(raw) if c.end > c.start]
    candidates.sort(key=lambda c: c.score, reverse=True)
    candidates = candidates[:20]
    if not candidates:
        raise RuntimeError("o Claude não encontrou candidatos — tente ajustar duração ou instruções")

    if opts.get("visual_review", True):
        reviewed = _visual_review(job_id, backend, candidates, transcript, events, style)
        _accept_plan(job_id, {"clips": reviewed})
    else:
        _accept_plan(job_id, raw)


def _visual_review(job_id: str, backend: str, candidates, transcript, events, style) -> list[dict]:
    job = get(job_id)
    src, opts = job["source"], job["options"]
    review_dir = _dir(job_id) / "review"
    shutil.rmtree(review_dir, ignore_errors=True)
    images, windows = [], []
    for i, c in enumerate(candidates, 1):
        update(job_id, status="reviewing", progress=(i - 1) / len(candidates),
               message=f"Separando frames do candidato {i} de {len(candidates)}…")
        ws, we = max(0.0, c.start - 4), min(src["duration"], c.end + 4)
        img = frames.grid(Path(src["path"]), ws, we, review_dir / f"c{i:02d}.jpg", src["width"], src["height"])
        images.append(img)
        windows.append((ws, we))
    update(job_id, progress=0.0, message=f"Claude assistindo aos {len(candidates)} candidatos…")
    system = picker.REVIEW_SYSTEM
    if style and style.get("guide"):
        system += f"\n\nGuia de estilo a seguir (\"{style['name']}\"):\n{style['guide']}"
    prompt = picker.review_prompt(candidates, transcript, events, windows, [p.name for p in images], opts)
    raw = picker.ask(backend, system, prompt, picker.REVIEW_SCHEMA, images)
    (_dir(job_id) / "review.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    out = []
    for item in raw.get("clips", []):
        idx = item.get("candidate")
        if isinstance(idx, int) and 1 <= idx <= len(candidates):
            item.setdefault("hashtags", candidates[idx - 1].hashtags)
            item["review_image"] = f"{job_id}/review/c{idx:02d}.jpg"
        out.append(item)
    return out


def submit_manual(job_id: str, raw: str) -> None:
    job = get(job_id)
    if not job or job["status"] not in ("waiting_manual", "done", "error"):
        raise ValueError("este job não está esperando a resposta do Claude")
    clips = picker.parse_plan(raw)  # valida antes de enfileirar
    (_dir(job_id) / "candidates.json").write_text(raw, encoding="utf-8")
    update(job_id, status="queued", message="Na fila", error=None)
    _worker.submit(_guard, job_id, _accept_plan, {"clips": [c.model_dump() for c in clips]})


def _accept_plan(job_id: str, raw: dict) -> None:
    job = get(job_id)
    opts, src = job["options"], job["source"]
    transcript = json.loads((_dir(job_id) / "transcript.json").read_text(encoding="utf-8"))
    words = transcribe.all_words(transcript)
    images = {c.get("candidate"): c.get("review_image") for c in raw.get("clips", []) if c.get("candidate")}
    clips = picker.snap_clips(picker.parse_plan(raw), words, src["duration"], opts["min_len"], opts["max_len"])
    clips = clips[: int(opts["num_clips"])]
    if not clips:
        raise RuntimeError("nenhum candidato passou na revisão — tente outras instruções ou desligue a revisão visual")
    for i, c in enumerate(clips, 1):
        c.update({"n": i, "status": "pending", "rev": 0, "review_image": images.get(c["candidate"])})
    update(job_id, clips=clips, status="rendering", progress=0.0)
    _render_all(job_id, words)


def _render_all(job_id: str, words: list[dict]) -> None:
    job = get(job_id)
    total = len(job["clips"])
    for idx in range(total):
        update(job_id, message=f"Renderizando clipe {idx + 1} de {total}…", progress=idx / total)
        _render_one(job_id, idx, words)
    failed = sum(c["status"] == "error" for c in get(job_id)["clips"])
    update(job_id, status="done", progress=1.0,
           message=f"{total - failed} clipes prontos" + (f", {failed} com erro" if failed else ""))


def _set_clip(job_id: str, idx: int, **fields) -> None:
    with _lock:
        _jobs[job_id]["clips"][idx].update(fields)
        _save(_jobs[job_id])


def _render_one(job_id: str, idx: int, words: list[dict]) -> None:
    job = get(job_id)
    clip = job["clips"][idx]
    _set_clip(job_id, idx, status="rendering", error=None)
    out_dir = _dir(job_id) / "clips" / f"{clip['n']:02d}"
    opts = {**job["options"], **clip.get("overrides", {})}
    opts.setdefault("title_text", clip["title"])
    try:
        info = render.render_clip(Path(job["source"]["path"]), job["source"], clip, out_dir, words, opts)
        _set_clip(job_id, idx, status="done", rev=clip.get("rev", 0) + 1,
                  file=f"{job_id}/clips/{clip['n']:02d}/clip.mp4",
                  thumb=f"{job_id}/clips/{clip['n']:02d}/thumb.jpg", **info)
    except Exception as exc:
        traceback.print_exc()
        _set_clip(job_id, idx, status="error", error=str(exc))


def _clip_index(job: dict, n: int) -> int:
    idx = next((i for i, c in enumerate(job["clips"]) if c["n"] == n), None)
    if idx is None:
        raise KeyError(n)
    return idx


def rerender(job_id: str, n: int, changes: dict) -> None:
    job = get(job_id)
    if not job:
        raise KeyError(job_id)
    idx = _clip_index(job, n)
    clip = job["clips"][idx]
    dur = job["source"]["duration"]
    start = max(0.0, float(changes.get("start", clip["start"])))
    end = min(dur, float(changes.get("end", clip["end"])))
    if end - start < 1:
        raise ValueError("o clipe precisa ter pelo menos 1 segundo")
    overrides = dict(clip.get("overrides", {}))
    for key in CLIP_OVERRIDES:
        if key in changes:
            overrides[key] = changes[key]
    if "word_fixes" in overrides and not isinstance(overrides["word_fixes"], dict):
        raise ValueError("word_fixes precisa ser um objeto {segundo: texto}")
    fields = {"start": round(start, 2), "end": round(end, 2), "overrides": overrides, "status": "pending"}
    if "title" in changes:
        fields["title"] = str(changes["title"])[:200]
    _set_clip(job_id, idx, **fields)
    _worker.submit(_render_one, job_id, idx, _words(job_id))


def _words(job_id: str) -> list[dict]:
    transcript = json.loads((_dir(job_id) / "transcript.json").read_text(encoding="utf-8"))
    return transcribe.all_words(transcript)


def reselect(job_id: str, opts: dict) -> None:
    """Pede novos cortes ao Claude reaproveitando a transcrição (ex.: com outras instruções)."""
    job = get(job_id)
    if not job or job["status"] not in ("done", "error", "waiting_manual"):
        raise ValueError("espere o processamento atual terminar")
    shutil.rmtree(_dir(job_id) / "clips", ignore_errors=True)
    update(job_id, options={**job["options"], **opts}, clips=[], status="queued",
           message="Na fila", error=None, progress=0.0)
    _worker.submit(_guard, job_id, _run_pipeline)


# ------------------------------------------------------------------ editor

EDITOR_MARGIN = 20.0


def editor_data(job_id: str, n: int) -> dict:
    """Tudo que o editor precisa: prévia leve do trecho com folga, volume e palavras."""
    job = get(job_id)
    if not job:
        raise KeyError(job_id)
    clip = job["clips"][_clip_index(job, n)]
    src = job["source"]
    clip_dir = _dir(job_id) / "clips" / f"{n:02d}"
    meta_file = clip_dir / "proxy.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else None
    if not meta or clip["start"] < meta["start"] + 2 or clip["end"] > meta["end"] - 2 or not (clip_dir / meta["file"]).exists():
        ws = max(0.0, clip["start"] - EDITOR_MARGIN)
        we = min(src["duration"], clip["end"] + EDITOR_MARGIN)
        name = f"proxy_{int(ws * 10)}_{int(we * 10)}.mp4"
        for old in clip_dir.glob("proxy_*.mp4"):
            old.unlink(missing_ok=True)
        render.proxy(Path(src["path"]), src, ws, we, clip_dir / name)
        meta = {"start": ws, "end": we, "file": name}
        meta_file.write_text(json.dumps(meta), encoding="utf-8")
    loud_file = _dir(job_id) / "loudness.json"
    loud = json.loads(loud_file.read_text(encoding="utf-8")) if loud_file.exists() else []
    fixes = clip.get("overrides", {}).get("word_fixes", {})
    words = [
        {**w, "fixed": fixes.get(f"{w['s']:.2f}")}
        for w in _words(job_id) if meta["start"] <= w["s"] <= meta["end"]
    ]
    return {
        "window": [meta["start"], meta["end"]],
        "proxy": f"{job_id}/clips/{n:02d}/{meta['file']}",
        "hop": signals.HOP,
        "loudness": signals.window(loud, meta["start"], meta["end"]),
        "words": words,
        "clip": clip,
        "options": {**job["options"], **clip.get("overrides", {})},
        "source": {k: src[k] for k in ("width", "height", "duration")},
    }


# ------------------------------------------------------------------ estilos

def create_style(name: str, urls: list[str], notes: str, backend: str = "auto") -> dict:
    style = styles.new(JOBS_DIR, name, urls, notes)
    _worker.submit(_learn_style_guard, style["id"], backend)
    return style


def _learn_style_guard(style_id: str, backend: str) -> None:
    try:
        _learn_style(style_id, backend)
    except Exception as exc:
        traceback.print_exc()
        st = styles.get(JOBS_DIR, style_id)
        if st:
            st.update(status="error", error=str(exc), message="Erro")
            styles.save(JOBS_DIR, st)


def _learn_style(style_id: str, backend: str) -> None:
    st = styles.get(JOBS_DIR, style_id)

    def progress(msg: str) -> None:
        st["message"] = msg
        styles.save(JOBS_DIR, st)

    backend = picker.resolve_backend(backend)
    if backend == "manual":
        raise RuntimeError("aprender estilo precisa do Claude Code logado ou de uma chave de API")
    work = JOBS_DIR / "_styles" / style_id
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    refs, images = [], []
    for i, url in enumerate(st["sources"], 1):
        progress(f"Baixando referência {i} de {len(st['sources'])}…")
        ref_dir = work / f"ref{i}"
        ref_dir.mkdir()
        video, title = media.download(url, ref_dir, max_height=480)
        info = media.probe(video)
        if info["duration"] > 600:
            raise RuntimeError(f"a referência {i} tem {info['duration'] / 60:.0f} min — use cortes curtos (até 10 min)")
        progress(f"Assistindo referência {i}: transcrevendo e separando frames…")
        events: list[dict] = []
        text = ""
        if info["has_audio"]:
            wav = media.extract_audio(video, ref_dir / "audio.wav")
            tr = transcribe.transcribe(wav, "large-v3-turbo", "", info["duration"])
            events = signals.audio_events(signals.loudness(wav), transcribe.all_words(tr))
            text = picker.format_transcript(tr, events)
        img = frames.grid(video, 0, info["duration"], work / f"ref{i}.jpg", info["width"], info["height"],
                          n=min(20, max(8, int(info["duration"] / 2))))
        images.append(img)
        refs.append({"title": title or url, "duration": info["duration"], "transcript": text, "image_name": img.name})
        shutil.rmtree(ref_dir, ignore_errors=True)  # guarda só a folha de frames
    progress("Claude estudando as referências…")
    raw = picker.ask(backend, styles.LEARN_SYSTEM, styles.learn_prompt(refs, st.get("notes", "")),
                     styles.LEARN_SCHEMA, images)
    st.update(guide=raw["guide"].strip(), summary=raw.get("summary", "").strip(), status="ready",
              message="Pronto", error=None,
              images=[f"_styles/{style_id}/{p.name}" for p in images])
    styles.save(JOBS_DIR, st)


def prompt_text(job_id: str) -> str:
    f = _dir(job_id) / "prompt.txt"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def transcript(job_id: str) -> dict | None:
    f = _dir(job_id) / "transcript.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
