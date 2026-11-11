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

from . import captions, edit, frames, media, paths, picker, render, signals, styles, titles, to_studio, transcribe

JOBS_DIR = paths.JOBS_DIR
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
    "keyword_highlight": True,
    "keyword_color": "#39E75F",
    "title_overlay": False,
    "title_mode": "always",
    "title_position": "top",
    "title_style": "impacto",
    "auto_zoom": True,
    "remove_pauses": False,
    "normalize_audio": True,
    "voice_enhance": True,
}

# o que o editor / "aplicar a todos" pode mudar em um clipe (sobrepõe as opções do job)
STYLE_KEYS = ("layout", "crop_x", "captions", "caption_preset", "caption_color", "caption_upper",
              "caption_position", "keyword_highlight", "keyword_color", "title_overlay", "title_mode",
              "title_position", "title_style", "auto_zoom", "remove_pauses", "normalize_audio",
              "voice_enhance")
CLIP_OVERRIDES = (*STYLE_KEYS, "title_text", "word_fixes", "word_adds")
SETTINGS_FILE = JOBS_DIR / "_settings.json"


def defaults() -> dict:
    """Opções padrão de projetos novos: as de fábrica + o que o usuário salvou como padrão."""
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        saved = {}
    return {**DEFAULT_OPTS, **{k: v for k, v in saved.items() if k in DEFAULT_OPTS}}


def save_defaults(values: dict) -> dict:
    current = defaults()
    current.update({k: v for k, v in values.items() if k in STYLE_KEYS and k != "crop_x"})
    changed = {k: v for k, v in current.items() if DEFAULT_OPTS.get(k) != v}
    SETTINGS_FILE.write_text(json.dumps(changed, ensure_ascii=False, indent=2), encoding="utf-8")
    return current

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


def busy() -> bool:
    """Há job, render de clipe ou aprendizado de estilo em andamento (fechar o app os interromperia)."""
    with _lock:
        working = any(
            j["status"] not in ("done", "error", "waiting_manual")
            or any(c.get("status") in ("pending", "rendering") for c in j["clips"])
            for j in _jobs.values()
        )
    from . import posedit  # importado aqui: posedit importa jobs
    return working or posedit.busy() or any(st.get("status") == "learning" for st in styles.list_styles(JOBS_DIR))


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
        "options": {**defaults(), **opts}, "backend": None,
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
    system = picker.system_prompt(opts["content_type"], style) + titles.guide(JOBS_DIR, options=False)
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
    system += titles.guide(JOBS_DIR)
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


def retitle(job_id: str) -> int:
    """Gera títulos novos para os clipes já prontos, sem refazer os cortes."""
    job = get(job_id)
    if not job:
        raise KeyError(job_id)
    if job["status"] not in ("done", "error") or not job["clips"]:
        raise ValueError("espere o processamento terminar")
    backend = picker.resolve_backend(job.get("backend") or job["options"].get("backend", "auto"))
    if backend == "manual":
        raise ValueError("gerar títulos precisa do Claude Code logado ou de ANTHROPIC_API_KEY")
    update(job_id, status="titling", progress=0.0, error=None, message="Claude escrevendo títulos…")
    _worker.submit(_guard, job_id, _retitle, backend)
    return len(job["clips"])


def _retitle(job_id: str, backend: str) -> None:
    job = get(job_id)
    words = _words(job_id)
    speech = {c["n"]: " ".join(w["w"] for w in words if c["start"] <= w["s"] < c["end"]) for c in job["clips"]}
    prompt = titles.retitle_prompt(job["clips"], speech)
    raw = picker.ask(backend, titles.RETITLE_SYSTEM + titles.guide(JOBS_DIR), prompt, titles.RETITLE_SCHEMA)
    (_dir(job_id) / "titles.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    changed, to_render = [], []
    for item in raw.get("clips", []):
        job = get(job_id)
        idx = next((i for i, c in enumerate(job["clips"]) if c["n"] == item.get("n")), None)
        if idx is None:
            continue
        clip = job["clips"][idx]
        picked = titles.choose(item.get("title_options"), item.get("title", ""))
        if not picked["title"]:
            continue
        old = clip["title"]
        if old and all(o["title"] != old for o in picked["title_options"]):
            # o título anterior continua disponível como sugestão no editor
            picked["title_options"].append({"title": old, "formula": titles.MANUAL, "score": 0, "problems": []})
        fields = dict(picked)
        if (item.get("post") or "").strip():
            fields["post"] = item["post"].strip()
        overrides = dict(clip.get("overrides", {}))
        if "title_text" in overrides:
            overrides["title_text"] = picked["title"]
            fields["overrides"] = overrides
        opts = {**job["options"], **overrides}
        if opts.get("title_overlay") and picked["title"] != old:
            fields["status"] = "pending"  # o título está gravado no vídeo: precisa renderizar de novo
            to_render.append(idx)
        _set_clip(job_id, idx, **fields)
        changed.append({**clip, **fields})
    titles.record(JOBS_DIR, job_id, changed)

    for i, idx in enumerate(to_render):
        update(job_id, status="rendering", progress=i / len(to_render),
               message=f"Renderizando com o título novo {i + 1} de {len(to_render)}…")
        _render_one(job_id, idx, words)
    update(job_id, status="done", progress=1.0,
           message=f"Títulos novos em {len(changed)} clipes" + (f", {len(to_render)} renderizados de novo" if to_render else ""))


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
    titles.record(JOBS_DIR, job_id, clips)
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


def _edit_plan(job_id: str, clip: dict, opts: dict, words: list[dict]) -> tuple[list | None, list[float]]:
    """Jump cuts (pausas mortas) e momentos de zoom de um clipe."""
    loud_file = _dir(job_id) / "loudness.json"
    loud = json.loads(loud_file.read_text(encoding="utf-8")) if loud_file.exists() else []
    start, end = clip["start"], clip["end"]
    zooms: list[float] = []
    if opts.get("auto_zoom", True):
        zooms = [r for r in clip.get("reactions") or [] if start <= r <= end]
        if not zooms and loud:  # sem dica do Claude: usa o maior pico de volume do trecho
            peaks = [e for e in signals.audio_events(loud, words)
                     if start <= e["t"] <= end and e["kind"] in ("grito", "som_alto") and e["db"] >= 9]
            if peaks:
                zooms = [max(peaks, key=lambda e: e["db"])["t"]]
    keep = None
    if opts.get("remove_pauses") and loud:
        protect = [(z - 0.5, z + 2.5) for z in zooms]  # nunca corta dentro da reação
        if clip.get("peak"):
            protect.append((clip["peak"] - 1.0, clip["peak"] + 4.0))
        keep = edit.keep_intervals(words, loud, signals.talk_level(loud, words), start, end, protect)
    return keep, zooms


def _render_one(job_id: str, idx: int, words: list[dict]) -> None:
    job = get(job_id)
    clip = job["clips"][idx]
    _set_clip(job_id, idx, status="rendering", error=None)
    out_dir = _dir(job_id) / "clips" / f"{clip['n']:02d}"
    opts = {**job["options"], **clip.get("overrides", {})}
    opts.setdefault("title_text", clip["title"])
    opts["keywords"] = clip.get("keywords") or []
    try:
        # legenda manual conta como fala: o corte de pausas não apaga o trecho dela
        keep, zooms = _edit_plan(job_id, clip, opts, captions.with_adds(words, opts.get("word_adds")))
        info = render.render_clip(Path(job["source"]["path"]), job["source"], clip, out_dir, words, opts,
                                  keep=keep, zooms=zooms)
        _set_clip(job_id, idx, status="done", rev=clip.get("rev", 0) + 1,
                  file=f"{job_id}/clips/{clip['n']:02d}/clip.mp4",
                  thumb=f"{job_id}/clips/{clip['n']:02d}/thumb.jpg", **info)
    except Exception as exc:
        traceback.print_exc()
        _set_clip(job_id, idx, status="error", error=str(exc))


def studio_tracks(job_id: str, n: int, seg_start: float, asset_id: str) -> dict:
    """Faixas do Estúdio para abrir o clipe n como ele foi renderizado (ver to_studio)."""
    job = get(job_id)
    if not job:
        raise KeyError(job_id)
    clip = job["clips"][_clip_index(job, n)]
    opts = {**job["options"], **clip.get("overrides", {})}
    opts.setdefault("title_text", clip["title"])
    opts["keywords"] = clip.get("keywords") or []
    words = _words(job_id)
    keep, zooms = _edit_plan(job_id, clip, opts, captions.with_adds(words, opts.get("word_adds")))
    return to_studio.build(job, clip, opts, words, keep, zooms, seg_start, asset_id)


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
    if "word_adds" in overrides:
        overrides["word_adds"] = _clean_adds(overrides["word_adds"])
    fields = {"start": round(start, 2), "end": round(end, 2), "overrides": overrides, "status": "pending"}
    if "title" in changes:
        fields["title"] = str(changes["title"]).strip()[:200]
        if fields["title"] != clip["title"]:
            fields["title_formula"] = titles.formula_of(clip, fields["title"])
    _set_clip(job_id, idx, **fields)
    if "title_formula" in fields:
        titles.record(JOBS_DIR, job_id, [{**clip, **fields}])
    _worker.submit(_render_one, job_id, idx, _words(job_id))


def _clean_adds(adds) -> list[dict]:
    """Legendas adicionadas no editor: [{w, s, e}] em segundos do vídeo original."""
    if not isinstance(adds, list):
        raise ValueError("word_adds precisa ser uma lista [{w, s, e}]")
    out = []
    for a in adds:
        try:
            text, s, e = str(a.get("w") or "").strip()[:200], float(a["s"]), float(a["e"])
        except (AttributeError, KeyError, TypeError, ValueError):
            raise ValueError("cada legenda adicionada precisa de texto, início e fim") from None
        if text and e > s:
            out.append({"w": text, "s": round(s, 3), "e": round(e, 3)})
    return sorted(out, key=lambda a: a["s"])


def apply_all(job_id: str, changes: dict) -> int:
    """Aplica um "modelo" (legenda, layout, efeitos) a todos os clipes e renderiza de novo."""
    job = get(job_id)
    if not job:
        raise KeyError(job_id)
    style = {k: v for k, v in changes.items() if k in STYLE_KEYS}
    if not style:
        raise ValueError("nada para aplicar")
    words = _words(job_id)
    with _lock:
        for clip in _jobs[job_id]["clips"]:
            clip["overrides"] = {**clip.get("overrides", {}), **style}
            clip["status"] = "pending"
        _save(_jobs[job_id])
    for idx in range(len(job["clips"])):
        _worker.submit(_render_one, job_id, idx, words)
    return len(job["clips"])


def create_clip(job_id: str, start: float, end: float, title: str = "") -> dict:
    """Clipe feito à mão (ex.: a partir de uma busca na transcrição)."""
    job = get(job_id)
    if not job:
        raise KeyError(job_id)
    dur = job["source"].get("duration") or 0
    start, end = max(0.0, float(start)), min(dur, float(end))
    if end - start < 1:
        raise ValueError("o clipe precisa ter pelo menos 1 segundo")
    words = _words(job_id)
    text = " ".join(w["w"] for w in words if start <= w["s"] <= end)
    n = max((c["n"] for c in job["clips"]), default=0) + 1
    clip = {
        "n": n, "candidate": None, "manual": True, "status": "pending", "rev": 0,
        "title": (title or text[:60] or f"Clipe {n}").strip(), "start": round(start, 2), "end": round(end, 2),
        "hook": "", "reason": "Clipe criado manualmente.", "visual": "", "score": 0, "scores": {},
        "hashtags": [], "peak": None, "reactions": [], "keywords": [], "post": "",
    }
    with _lock:
        _jobs[job_id]["clips"].append(clip)
        _save(_jobs[job_id])
    _worker.submit(_render_one, job_id, len(job["clips"]), words)
    return clip


def delete_clip(job_id: str, n: int) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        idx = _clip_index(job, n)
        if job["clips"][idx]["status"] in ("pending", "rendering"):
            raise ValueError("espere a renderização terminar")
        job["clips"].pop(idx)
        _save(job)
    shutil.rmtree(_dir(job_id) / "clips" / f"{n:02d}", ignore_errors=True)


def export_zip(job_id: str) -> Path:
    """Todos os clipes prontos + legendas .srt + um arquivo com títulos e descrições para postar."""
    import zipfile

    job = get(job_id)
    if not job:
        raise KeyError(job_id)
    done = [c for c in job["clips"] if c["status"] == "done"]
    if not done:
        raise ValueError("nenhum clipe pronto para baixar")
    out = _dir(job_id) / "export.zip"
    lines = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:  # mp4 já é comprimido
        for c in done:
            base = f"{c['n']:02d}-{''.join(ch for ch in c['title'] if ch.isalnum() or ch in ' -_')[:50].strip()}"
            clip_dir = _dir(job_id) / "clips" / f"{c['n']:02d}"
            z.write(clip_dir / "clip.mp4", f"{base}.mp4")
            if (clip_dir / "clip.srt").exists():
                z.write(clip_dir / "clip.srt", f"{base}.srt")
            post = c.get("post") or f"{c['title']}\n\n{' '.join(c.get('hashtags', []))}"
            lines.append(f"### {base}  (score {c['score']})\nTítulo: {c['title']}\n{post}\n")
        z.writestr("posts.txt", "\n".join(lines))
    return out


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
