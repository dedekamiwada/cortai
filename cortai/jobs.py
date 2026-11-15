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

from . import capa, captions, edit, frames, hud, media, paths, picker, reframe, render, signals, styles, titles, to_studio, transcribe

JOBS_DIR = paths.JOBS_DIR
picker.USAGE_FILE = JOBS_DIR / "_uso" / "uso.csv"  # consumo de tokens por chamada (otimização com números)
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
    "voice_track": 0,                  # faixa de áudio da voz (1, 2…); 0 = automática (faixa "mic"/"voz" se houver)
    "explore": False,                  # +1 corte de um tipo pouco testado no canal, marcado como exploração
    "mode": "clips",                   # "clips" (vários cortes) ou "highlights" (melhores momentos num vídeo)
    "highlight_basis": "fala_imagem",  # "fala_imagem" ou "imagem"
    "pos_susto": "",                   # terror: "congelar", "pb" ou "congelar_pb" do susto até o fim
    "pos_susto_musica": False,         # trilha da biblioteca (musicas/pos_susto) a partir do susto
    "cover_auto": True,                # capa (thumbnail) pronta para cada clipe
    "cover_style": capa.DEFAULT_STYLE,
    "cover_handle": "",                # ex.: @_saitox_, escrito embaixo do título da capa
    "cover_in_video": False,           # também gravar a capa no fim do vídeo
    "serie": "",       # bordão da série do resumo narrado, com {n} = número do episódio
    "serie_n": 1,      # próximo episódio (sobe sozinho a cada resumo pronto)
}

# o que o editor / "aplicar a todos" pode mudar em um clipe (sobrepõe as opções do job)
STYLE_KEYS = ("layout", "crop_x", "captions", "caption_preset", "caption_color", "caption_upper",
              "caption_position", "keyword_highlight", "keyword_color", "title_overlay", "title_mode",
              "title_position", "title_style", "auto_zoom", "remove_pauses", "normalize_audio",
              "voice_enhance", "pos_susto", "pos_susto_musica")
CLIP_OVERRIDES = (*STYLE_KEYS, "title_text", "word_fixes", "word_adds")
COVER_KEYS = ("cover_auto", "cover_style", "cover_handle", "cover_in_video")  # padrões da capa
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
    current.update({k: v for k, v in values.items() if (k in STYLE_KEYS or k in COVER_KEYS) and k != "crop_x"})
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
        if job.get("status") not in ("done", "error", "waiting_manual", "waiting_match"):
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
            j["status"] not in ("done", "error", "waiting_manual", "waiting_match")
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
           upload_path: Path | None = None, seed_files: dict[str, Path] | None = None) -> dict:
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

    opts = {**defaults(), **opts}
    if opts.get("mode") == "highlights" and (opts.get("serie") or "").strip():
        opts["serie"] = opts["serie"].strip()
        opts["serie_n"] = _reserve_episode(opts["serie"], opts.get("serie_n"))
    job = {
        "id": job_id, "name": name or source.get("url") or Path(source["path"]).name,
        "created": time.time(), "updated": time.time(),
        "status": "queued", "progress": 0.0, "message": "Na fila",
        "options": opts, "backend": None,
        "source": source, "clips": [], "error": None,
    }
    for name_, path_ in (seed_files or {}).items():  # transcrição/volume já calculados da mesma fonte
        shutil.copy(path_, d / name_)
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


VOICE_NAMES = ("mic", "voz", "voice", "micro", "fala")


def _voice_track(job: dict) -> tuple[int | None, str]:
    """Faixa de áudio para transcrever e medir volume. Com várias faixas (OBS), a do microfone tem a voz
    sem o som do jogo por cima: Whisper erra menos e os picos de volume são do streamer, não de explosões."""
    tracks = job["source"].get("audio_tracks") or []
    want = int(job["options"].get("voice_track") or 0)
    if 1 <= want <= len(tracks):
        return want - 1, f"faixa {want}" + (f" ({tracks[want - 1]['titulo']})" if tracks[want - 1]["titulo"] else "")
    if len(tracks) > 1:
        for i, t in enumerate(tracks):
            if any(k in t["titulo"].lower() for k in VOICE_NAMES):
                return i, f"faixa {i + 1} ({t['titulo']})"
    return None, ""


def _ensure_audio(job_id: str, path: Path, duration: float) -> Path:
    audio = _dir(job_id) / "audio.wav"
    if not audio.exists():
        track, label = _voice_track(get(job_id))
        update(job_id, status="transcribing", progress=0.0,
               message=f"Extraindo a voz da {label}…" if label else "Extraindo áudio…")
        tmp = audio.with_suffix(".tmp.wav")
        media.extract_audio(path, tmp, duration, on_progress=lambda p: _throttled_progress(job_id, p), track=track)
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


def _game_events(job_id: str, backend: str) -> list[dict]:
    """Gols, começos e fins de partida lidos no placar do jogo (hud.py), com cache em hud_events.json.

    Só roda em live de jogo e com o Claude disponível (calibração + leitura do placar). Vídeo sem placar
    reconhecível (outro jogo, cena sem HUD) devolve lista vazia e não tenta de novo.
    """
    job = get(job_id)
    d, src = _dir(job_id), job["source"]
    cache = d / "hud_events.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8")).get("events", [])
    if job["options"].get("content_type") != "game" or backend == "manual":
        return []
    path = Path(src["path"])
    update(job_id, status="reviewing", progress=0.0, message="Procurando o placar do jogo na tela…")
    dur = src["duration"]
    calib = hud.calibrate(backend, path, src, [dur * f for f in (0.3, 0.5, 0.7, 0.85)], d / "hud")
    if not calib:
        cache.write_text(json.dumps({"calib": None, "events": []}), encoding="utf-8")
        return []
    update(job_id, message="Lendo o placar da live inteira (gols e partidas)…")
    events, spans = hud.read_events(backend, path, src, calib, 0.0, dur, d / "hud")
    cache.write_text(json.dumps({"calib": calib, "events": events, "trechos": spans}, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    return events


def _timeline(job_id: str, backend: str, loud: list[float], words: list[dict]) -> list[dict]:
    """Marcadores para o Claude: sinais de áudio de sempre + gols do placar + reações fortes (z robusto)."""
    events = signals.audio_events(loud, words)
    for e in _game_events(job_id, backend):
        if e["tipo"] in ("gol_nosso", "gol_deles", "inicio", "fim_do_tempo"):
            events.append({"t": e["t"], "kind": e["tipo"], "dur": round(e["fim_pausa"] - e["t"], 1),
                           "placar": e.get("placar", ""), "relogio": e.get("relogio", ""), "fim_pausa": e["fim_pausa"]})
    for pk in signals.robust_peaks(loud):
        events.append({"t": pk["t"], "kind": "reacao", "dur": 1.0, "z": pk["z"]})
    for m in get(job_id)["source"].get("chapters") or []:
        events.append({"t": m["t"], "kind": "marcador", "dur": 0.0, "titulo": m["titulo"]})
    events.sort(key=lambda e: e["t"])
    return events


MARKER_LAG = 45  # o atalho é apertado DEPOIS do lance: procura o momento até 45 s antes do marcador


def _marked(clip: dict, markers: list[dict]) -> dict | None:
    return next((m for m in markers if clip["start"] - 5 <= m["t"] <= clip["end"] + MARKER_LAG), None)


def _anchors(events: list[dict]) -> list[dict]:
    """Eventos que podem ancorar o pico de um corte (para encaixar o segundo exato)."""
    return [e for e in events if e["kind"] in ("gol_nosso", "gol_deles", "reacao")]


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
    if opts.get("mode") == "highlights":
        _run_highlights(job_id)
        return
    if not src["has_audio"]:
        raise RuntimeError("o vídeo não tem áudio — não dá para transcrever")

    transcript, loud = _transcript_and_signals(job_id)
    if not transcript["segments"]:
        raise RuntimeError("nenhuma fala detectada no vídeo")
    words = transcribe.all_words(transcript)
    backend = picker.resolve_backend(opts["backend"])
    update(job_id, backend=backend)
    events = _timeline(job_id, backend, loud, words)

    style = styles.get(JOBS_DIR, opts.get("style_id"))
    system = (picker.system_prompt(opts["content_type"], style) + titles.guide(JOBS_DIR, options=False)
              + titles.clip_guide(JOBS_DIR))
    prompt = picker.build_prompt(transcript, events, src["duration"], opts)
    (d / "prompt.txt").write_text(picker.manual_prompt(system, prompt), encoding="utf-8")

    if backend == "manual":
        update(job_id, status="waiting_manual", progress=0.0,
               message="Cole o prompt no Claude e traga a resposta de volta")
        return

    who = "Claude Code" if backend == "cli" else "API"
    update(job_id, status="selecting", progress=0.0,
           message=f"Claude ({who}) lendo a transcrição e os picos de áudio…")
    raw = picker.ask(backend, system, prompt, picker.CANDIDATES_SCHEMA, step="cortes_candidatos")
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


# ------------------------------------------------------------------ melhores momentos da partida

HIGHLIGHT_CANDIDATES = 30  # resumo da partida: precisa cobrir todos os gols
MATCH_SPLIT_MIN = 15 * 60  # acima disso, "melhores momentos" sem trecho escolhido começa separando as partidas


def map_matches(job_id: str) -> None:
    job = get(job_id)
    if not job:
        raise KeyError(job_id)
    if job["status"] not in ("done", "error", "waiting_match") or not job["source"].get("duration"):
        raise ValueError("espere o processamento terminar")
    previous = "waiting_match" if job["status"] == "waiting_match" else "done" if job["clips"] else job["status"]
    update(job_id, status="mapping", progress=0.0, error=None, message="Na fila para separar as partidas…")
    _worker.submit(_guard, job_id, _map_matches, previous)


def _map_matches(job_id: str, then_status: str = "done") -> None:
    from . import highlights

    job = get(job_id)
    src = job["source"]
    backend = picker.resolve_backend(job.get("backend") or job["options"].get("backend", "auto"))
    if backend == "manual":
        raise RuntimeError("separar partidas precisa do Claude Code logado ou de ANTHROPIC_API_KEY")
    update(job_id, status="mapping", progress=0.0, message="Separando frames da live inteira…")
    sheets = highlights.scan_sheets(Path(src["path"]), src, _dir(job_id) / "mapa", 0.0, src["duration"],
                                    highlights.MAP_MAX_FRAMES, highlights.MAP_MIN_STEP)
    update(job_id, message=f"Claude separando as partidas ({len(sheets)} folhas de frames)…")
    raw = picker.ask(backend, highlights.MAP_SYSTEM, highlights.map_prompt(sheets, src["duration"]),
                     highlights.MAP_SCHEMA, [s[0] for s in sheets], step="partidas_mapa")
    (_dir(job_id) / "partidas.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    partidas = highlights.normalize_matches(raw, src["duration"])
    if not partidas:
        raise RuntimeError("não achei nenhuma partida nas imagens da live")
    update(job_id, partidas=partidas, status=then_status, progress=1.0,
           message=f"{len(partidas)} partidas encontradas" +
                   (" — escolha de qual fazer os melhores momentos" if then_status == "waiting_match" else ""))


def create_match_job(job_id: str, n: int, basis: str, serie: str | None = None) -> dict:
    """Novo projeto de melhores momentos só com o trecho de uma partida (reaproveita transcrição e volume)."""
    job = get(job_id)
    m = next((p for p in (job or {}).get("partidas") or [] if p["n"] == n), None)
    if not m:
        raise ValueError("partida não encontrada")
    d = _dir(job_id)
    seed = {}
    if (d / "loudness.json").exists():
        seed["loudness.json"] = d / "loudness.json"
        tfile = d / "transcript.json"
        if tfile.exists() and json.loads(tfile.read_text(encoding="utf-8")).get("segments"):
            seed["transcript.json"] = tfile
    if (d / "hud_events.json").exists():
        seed["hud_events.json"] = d / "hud_events.json"  # o placar da live inteira já foi lido
    opts = {**job["options"], "mode": "highlights", "range_start": m["inicio"], "range_end": m["fim"],
            "highlight_basis": basis if basis in ("fala_imagem", "imagem") else "fala_imagem",
            "serie": defaults().get("serie", "") if serie is None else serie, "serie_n": None}
    base = Path(job["name"]).stem if "." in job["name"] else job["name"]
    name = f"{base} — partida {n}" + (f" ({m['placar']})" if m["placar"] else "")
    return create(name, opts, local_path=job["source"]["path"], seed_files=seed)


def _run_highlights(job_id: str) -> None:
    """Partida inteira → candidatos (pela fala+imagem ou só pela imagem) → Claude assiste e monta a
    história → renderiza os lances → junta no short vertical e na versão horizontal + roteiro."""
    from . import highlights

    job = get(job_id)
    d, opts, src = _dir(job_id), job["options"], job["source"]
    path = Path(src["path"])
    start = float(opts.get("range_start") or 0)
    end = float(opts.get("range_end") or 0) or src["duration"]
    extra = (opts.get("instructions") or "").strip()
    backend = picker.resolve_backend(opts["backend"])
    update(job_id, backend=backend)
    if backend == "manual":
        raise RuntimeError("melhores momentos precisa do Claude Code logado ou de ANTHROPIC_API_KEY")
    if not opts.get("range_start") and not opts.get("range_end") and src["duration"] > MATCH_SPLIT_MIN:
        # live longa: primeiro separa as partidas e deixa o usuário escolher qual vira vídeo
        _map_matches(job_id, then_status="waiting_match")
        return

    if opts.get("highlight_basis") == "imagem":
        tfile, lfile = d / "transcript.json", d / "loudness.json"
        transcript = {"segments": []}
        if lfile.exists():
            loud = json.loads(lfile.read_text(encoding="utf-8"))
        else:
            loud = []
            if src["has_audio"]:
                audio = _ensure_audio(job_id, path, src["duration"])
                update(job_id, message="Medindo os picos do som do jogo…")
                loud = signals.loudness(audio)
                audio.unlink(missing_ok=True)
            lfile.write_text(json.dumps(loud), encoding="utf-8")
        tfile.write_text(json.dumps(transcript), encoding="utf-8")
        events = _timeline(job_id, backend, loud, [])
        update(job_id, status="reviewing", progress=0.0, message="Separando frames da partida inteira…")
        sheets = highlights.scan_sheets(path, src, d / "scan", start, end)
        update(job_id, status="selecting", message=f"Claude assistindo à partida ({len(sheets)} folhas de frames)…")
        raw = picker.ask(backend, highlights.SCAN_SYSTEM,
                         highlights.scan_prompt(sheets, events, HIGHLIGHT_CANDIDATES, extra),
                         picker.CANDIDATES_SCHEMA, [s[0] for s in sheets], step="resumo_varredura")
    else:
        if not src["has_audio"]:
            raise RuntimeError("o vídeo não tem áudio — use a opção 'só imagem'")
        transcript, loud = _transcript_and_signals(job_id)
        events = _timeline(job_id, backend, loud, transcribe.all_words(transcript))
        text = picker.format_transcript(transcript, events, start, end)
        update(job_id, status="selecting", progress=0.0, message="Claude lendo a partida e os picos de áudio…")
        raw = picker.ask(backend, picker.system_prompt("game", None),
                         highlights.transcript_candidates_prompt(text, src["duration"], HIGHLIGHT_CANDIDATES, extra),
                         picker.CANDIDATES_SCHEMA, step="resumo_candidatos")
    (d / "candidates.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    candidates = sorted((c for c in picker.parse_plan(raw) if c.end > c.start), key=lambda c: c.score, reverse=True)
    candidates = sorted(candidates[:HIGHLIGHT_CANDIDATES] + _missing_goals(events, candidates, start, end),
                        key=lambda c: c.start)
    if not candidates:
        raise RuntimeError("o Claude não achou lances — tente outras instruções")

    review_dir = d / "review"
    shutil.rmtree(review_dir, ignore_errors=True)
    images, windows = [], []
    for i, c in enumerate(candidates, 1):
        update(job_id, status="reviewing", progress=(i - 1) / len(candidates),
               message=f"Separando frames do lance {i} de {len(candidates)}…")
        ws, we = max(0.0, c.start - 3), min(src["duration"], c.end + 3)
        images.append(frames.grid(path, ws, we, review_dir / f"c{i:02d}.jpg", src["width"], src["height"],
                                  focus=c.peak))
        windows.append((ws, we))

    def context(a: float, b: float) -> str:
        if transcript["segments"]:
            return picker.format_transcript(transcript, events, a, b)
        return "\n".join(signals.describe(e) for e in events
                         if a <= e["t"] <= b and (e["kind"] in signals.LOUD_KINDS or e["kind"] in GAME_KINDS))

    update(job_id, progress=0.0, message=f"Claude assistindo aos {len(candidates)} lances e escrevendo o roteiro…")
    serie = (opts.get("serie") or "").strip()
    serie_txt = serie.replace("{n}", str(int(opts.get("serie_n") or 1))) if serie else ""
    raw = picker.ask(backend, highlights.REVIEW_SYSTEM,
                     highlights.review_prompt(candidates, windows, [p.name for p in images], context, extra, serie_txt),
                     highlights.REVIEW_SCHEMA, images, step="resumo_revisao")
    (d / "highlight-review.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    plan = highlights.normalize(raw, candidates, src["duration"])
    if not plan["lances"]:
        raise RuntimeError("nenhum lance passou na revisão — tente outras instruções")
    clips = []
    for i, x in enumerate(plan["lances"], 1):
        clips.append({
            "n": i, "status": "pending", "rev": 0, "title": x["label"] or f"Lance {i}",
            "start": x["start"], "end": x["end"], "peak": x["peak"], "reactions": x["reactions"],
            "keywords": [], "score": x["score"], "scores": {}, "visual": x["visual"],
            "reason": highlights.KINDS.get(x["tipo"], x["tipo"]) + (" · entra no short" if x["no_short"] else ""),
            "hook": "", "post": "", "hashtags": [], "title_options": [], "candidate": x["candidate"],
            "review_image": f"{job_id}/review/c{x['candidate']:02d}.jpg",
            "no_short": x["no_short"], "tipo": x["tipo"], "narracao": x["narracao"],
            # o lance é um trecho do resumo: título gravado em cada um quebraria a narração
            "overrides": {"title_overlay": False},
        })
    update(job_id, clips=clips, status="rendering", progress=0.0,
           highlight={"titulo": plan["titulo"], "gancho": plan["gancho"], "post": plan["post"],
                      "adversario": plan.get("adversario", ""), "serie": serie_txt})
    words = transcribe.all_words(transcript)
    for idx in range(len(clips)):
        update(job_id, message=f"Renderizando o lance {idx + 1} de {len(clips)} (vertical)…",
               progress=idx / len(clips) * 0.5)
        _render_one(job_id, idx, words)
    _compile_highlights(job_id)


GAME_KINDS = ("gol_nosso", "gol_deles", "inicio", "fim_do_tempo", "reacao")


def _reserve_episode(serie: str, n: int | None) -> int:
    """Número do episódio da série, reservado na CRIAÇÃO do projeto: várias partidas geradas de uma vez
    ficam 12, 13, 14… O próximo projeto já vem com o número seguinte, e o bordão fica salvo como padrão."""
    with _lock:
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            saved = {}
        ep = int(n) if n else int(saved.get("serie_n") or 1)
        saved["serie"] = serie
        saved["serie_n"] = max(int(saved.get("serie_n") or 1), ep + 1)
        SETTINGS_FILE.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        return ep


REDO_MARGIN = 20.0  # s de folga em volta do corte que o Claude pode usar para reposicionar


def redo_clip(job_id: str, n: int, instruction: str) -> None:
    job = get(job_id)
    if not job:
        raise KeyError(job_id)
    instruction = (instruction or "").strip()
    if not instruction:
        raise ValueError("escreva o que mudar (ex.: começa 3 s antes, termina na risada)")
    idx = _clip_index(job, n)
    if job["clips"][idx]["status"] in ("pending", "rendering"):
        raise ValueError("espere a renderização terminar")
    backend = picker.resolve_backend(job.get("backend") or job["options"].get("backend", "auto"))
    if backend == "manual":
        raise ValueError("refazer com instrução precisa do Claude Code logado ou de ANTHROPIC_API_KEY")
    _set_clip(job_id, idx, status="pending", error=None, redo={"instrucao": instruction, "resposta": "Refazendo…"})
    _worker.submit(_redo_clip, job_id, idx, instruction, backend)


def _redo_clip(job_id: str, idx: int, instruction: str, backend: str) -> None:
    try:
        job = get(job_id)
        clip, src = job["clips"][idx], job["source"]
        words = _words(job_id)
        loud_file = _dir(job_id) / "loudness.json"
        loud = json.loads(loud_file.read_text(encoding="utf-8")) if loud_file.exists() else []
        events = _timeline(job_id, backend, loud, words)
        a, b = max(0.0, clip["start"] - REDO_MARGIN), min(src["duration"], clip["end"] + REDO_MARGIN)
        img = frames.grid(Path(src["path"]), a, b, _dir(job_id) / "review" / f"redo-{clip['n']:02d}.jpg",
                          src["width"], src["height"], n=16, cols=4, focus=clip.get("peak"))
        transcript = json.loads((_dir(job_id) / "transcript.json").read_text(encoding="utf-8"))
        prompt = (f"Instrução do streamer: {instruction}\n\nCorte atual: \"{clip['title']}\" de {clip['start']:.2f}s a "
                  f"{clip['end']:.2f}s, pico em {clip.get('peak') or clip['start']:.2f}s.\nQuadros de {a:.1f}s a {b:.1f}s "
                  f"na imagem {img.name}.\n\n" + picker.format_transcript(transcript, events, a, b))
        raw = picker.ask(backend, picker.REDO_SYSTEM, prompt, picker.REDO_SCHEMA, [img], step="corte_refazer")
        new = picker.Clip(title=(raw.get("title") or clip["title"]).strip()[:200], start=float(raw["start"]),
                          end=float(raw["end"]), peak=float(raw.get("peak") or raw["start"]), score=clip.get("score", 50))
        # o pedido manda nas bordas: só encaixa nas palavras (sem as regras automáticas de contexto/replay)
        snapped = picker.snap_clips([new], words, src["duration"], 1, 600)
        if not snapped:
            raise RuntimeError("o Claude devolveu um corte vazio")
        s = snapped[0]
        peak, anchor = picker._snap_anchor(new, _anchors(events))
        anchor = {k: anchor[k] for k in ("kind", "t", "placar") if k in anchor} if anchor else None
        _set_clip(job_id, idx, start=s["start"], end=s["end"], peak=peak, anchor=anchor,
                  title=new.title, redo={"instrucao": instruction, "resposta": (raw.get("resposta") or "").strip()})
    except Exception as exc:
        traceback.print_exc()
        _set_clip(job_id, idx, status="error", error=f"não consegui refazer: {exc}")
        return
    _render_one(job_id, idx, _words(job_id))


def quality(job_id: str) -> dict:
    """Métricas de qualidade do projeto ANTES de postar (as da plataforma chegam tarde e com ruído): o que a
    pesquisa recomendou acompanhar por live."""
    job = get(job_id)
    if not job:
        raise KeyError(job_id)
    clips = [c for c in job["clips"] if c.get("status") == "done"]
    if not clips:
        return {}
    n = len(clips)
    fb = [c.get("feedback") or {} for c in clips]
    reasons: dict[str, int] = {}
    for f in fb:
        if f.get("veredito") == "rejeitado":
            reasons[f.get("motivo") or "outro"] = reasons.get(f.get("motivo") or "outro", 0) + 1
    tested = [c for c in clips if c.get("estranho")]
    lead = sorted(c["peak"] - c["start"] for c in clips if c.get("peak"))
    starts = _match_starts(job_id)
    match_of = lambda t: sum(1 for s0 in starts if s0 <= t)
    return {
        "cortes": n,
        "ancorados": sum(1 for c in clips if c.get("anchor")),
        "estranho_testados": len(tested),
        "estranho_ok": sum(1 for c in tested if c["estranho"].get("entende")),
        "contexto_tela": sum(1 for c in clips if c.get("contexto_tela")),
        "aprovados": sum(1 for f in fb if f.get("veredito") == "ok"),
        "rejeitados": sum(1 for f in fb if f.get("veredito") == "rejeitado"),
        "motivos": reasons,
        "segundos_ate_lance": round(lead[len(lead) // 2], 1) if lead else None,
        "partidas": len({match_of(c.get("peak") or c["start"]) for c in clips}) if starts else None,
        "pausas_removidas_s": round(sum(c.get("removed") or 0 for c in clips), 1),
        "duracao_media_s": round(sum(c.get("out_duration") or (c["end"] - c["start"]) for c in clips) / n, 1),
    }


def timeline(job_id: str) -> dict:
    """Linha do tempo da live para a tela: partidas e gols (placar), reações fortes e os cortes que já existem."""
    job = get(job_id)
    if not job:
        raise KeyError(job_id)
    d = _dir(job_id)
    hud_data = json.loads((d / "hud_events.json").read_text(encoding="utf-8")) if (d / "hud_events.json").exists() else {}
    loud = json.loads((d / "loudness.json").read_text(encoding="utf-8")) if (d / "loudness.json").exists() else []
    starts = _match_starts(job_id)
    return {
        "duration": job["source"].get("duration") or 0,
        "partidas": [round(t, 1) for t in starts],
        "eventos": [{k: e.get(k) for k in ("t", "tipo", "placar", "relogio", "fim_pausa")}
                    for e in hud_data.get("events", []) if e.get("tipo") in ("gol_nosso", "gol_deles", "fim_do_tempo")],
        "reacoes": signals.robust_peaks(loud),
        "cortes": [{"n": c["n"], "start": c["start"], "end": c["end"], "title": c["title"]} for c in job["clips"]],
    }


def save_script(job_id: str, key: str, text: str) -> dict:
    """Roteiro editado à mão: regrava o .txt e refaz o .srt a partir das linhas "[  12.3s] fala" (sem render)."""
    from . import highlights

    job = get(job_id)
    hl = (job or {}).get("highlight") or {}
    v = hl.get(key)
    if not v:
        raise ValueError("versão não encontrada")
    total = float(v.get("duration") or 0)
    srt = highlights.srt_from_script(text, total)
    if not srt:
        raise ValueError("nenhuma fala com tempo no formato [  12.3s] texto")
    (JOBS_DIR / v["roteiro"]).write_text(text, encoding="utf-8")
    (JOBS_DIR / v["srt"]).write_text(srt, encoding="utf-8")
    v["rev"] = int(v.get("rev", 0)) + 1
    hl[key] = v
    update(job_id, highlight=hl)
    return v


def _missing_goals(events: list[dict], candidates: list, start: float, end: float) -> list:
    """Gols do placar que o Claude não propôs: no resumo narrado todos os gols entram (o estilo do @howardrl)."""
    out = []
    for e in events:
        if e["kind"] not in ("gol_nosso", "gol_deles") or not start <= e["t"] <= end:
            continue
        if any(c.start - 2 <= e["t"] <= c.end + 2 for c in candidates):
            continue
        who = "Gol do time" if e["kind"] == "gol_nosso" else "Gol sofrido"
        out.append(picker.Clip(title=f"{who} {e.get('placar', '')}".strip(), start=max(start, e["t"] - 10),
                               end=min(end, e["t"] + 8), peak=e["t"], score=70,
                               reason=f"{who.lower()} lido no placar ({e.get('placar', '')}); entra para contar a partida"))
    return out


def compile_highlights(job_id: str) -> None:
    """Remonta os dois vídeos (depois de editar um lance, por exemplo)."""
    job = get(job_id)
    if not job or job["options"].get("mode") != "highlights" or not job["clips"]:
        raise ValueError("este projeto não é de melhores momentos")
    if job["status"] not in ("done", "error"):
        raise ValueError("espere o processamento terminar")
    update(job_id, status="rendering", progress=0.5, error=None, message="Remontando os vídeos…")
    _worker.submit(_guard, job_id, _compile_highlights)


def _compile_highlights(job_id: str) -> None:
    from . import highlights

    job = get(job_id)
    d, src, opts = _dir(job_id), job["source"], job["options"]
    hl = dict(job.get("highlight") or {})
    out_dir = d / "highlights"
    out_dir.mkdir(exist_ok=True)
    fps = min(src.get("fps") or 30, 60)
    done = sorted((c for c in job["clips"] if c.get("status") == "done"), key=lambda c: c["start"])
    if not done:
        raise RuntimeError("nenhum lance renderizado")

    short = [c for c in done if c.get("no_short")] or done
    update(job_id, status="rendering", message="Montando o short vertical…", progress=0.6)
    vsegs = [plain_clip(c["file"]) for c in short]
    card = f"VS {hl['adversario']}" if hl.get("adversario") else ""  # card do adversário na abertura
    vstarts = highlights.compile_video(vsegs, [], card, out_dir / "short-vertical.mp4", 1080, 1920, fps)

    hsegs = []
    for k, c in enumerate(done, 1):
        update(job_id, message=f"Renderizando o lance {k} de {len(done)} (horizontal)…",
               progress=0.7 + 0.2 * (k - 1) / len(done))
        out = out_dir / "h" / f"{c['n']:02d}.mp4"
        highlights.horizontal_segment(Path(src["path"]), src, c["start"], c["end"], out,
                                      opts.get("voice_enhance", True))
        hsegs.append(out)
    update(job_id, message="Montando a versão horizontal…", progress=0.92)
    hstarts = highlights.compile_video(hsegs, [], card, out_dir / "horizontal.mp4", 1920, 1080, fps)

    versions = {"vertical": (short, vsegs, vstarts, "short-vertical"), "horizontal": (done, hsegs, hstarts, "horizontal")}
    for key, (clips, segs, starts, name) in versions.items():
        durations = [highlights._duration(s) for s in segs]
        total = highlights._duration(out_dir / f"{name}.mp4")
        txt, srt = highlights.script(clips, starts, durations, total,
                                     hl.get("gancho", "") if key == "vertical" else "", hl.get("titulo", ""),
                                     "short vertical" if key == "vertical" else "horizontal")
        (out_dir / f"{name}-roteiro.txt").write_text(txt, encoding="utf-8")
        (out_dir / f"{name}-roteiro.srt").write_text(srt, encoding="utf-8")
        hl[key] = {"file": f"{job_id}/highlights/{name}.mp4", "duration": round(total, 1), "lances": len(clips),
                   "roteiro": f"{job_id}/highlights/{name}-roteiro.txt",
                   "srt": f"{job_id}/highlights/{name}-roteiro.srt",
                   "rev": int((hl.get(key) or {}).get("rev", 0)) + 1}
    shutil.rmtree(out_dir / "h", ignore_errors=True)
    update(job_id, highlight=hl, status="done", progress=1.0,
           message=f"Short de {hl['vertical']['duration']:.0f}s e horizontal de "
                   f"{hl['horizontal']['duration'] / 60:.1f} min prontos")

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
        img = frames.grid(Path(src["path"]), ws, we, review_dir / f"c{i:02d}.jpg", src["width"], src["height"],
                          focus=c.peak)
        images.append(img)
        windows.append((ws, we))
    update(job_id, progress=0.0, message=f"Claude assistindo aos {len(candidates)} candidatos…")
    system = picker.REVIEW_SYSTEM
    if style and style.get("guide"):
        system += f"\n\nGuia de estilo a seguir (\"{style['name']}\"):\n{style['guide']}"
    system += titles.guide(JOBS_DIR)
    prompt = picker.review_prompt(candidates, transcript, events, windows, [p.name for p in images], opts)
    raw = picker.ask(backend, system, prompt, picker.REVIEW_SCHEMA, images, step="cortes_revisao")
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
    raw = picker.ask(backend, titles.RETITLE_SYSTEM + titles.guide(JOBS_DIR), prompt, titles.RETITLE_SCHEMA, step="titulos")
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
        cover = clip.get("cover")
        if cover and not cover.get("custom_text") and idx not in to_render:
            try:
                _make_cover(job_id, idx, words=words, attach=True)
            except Exception as exc:
                traceback.print_exc()
                _set_clip(job_id, idx, cover_error=str(exc))
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
    loud_file = _dir(job_id) / "loudness.json"
    loud = json.loads(loud_file.read_text(encoding="utf-8")) if loud_file.exists() else []
    backend = job.get("backend") or "manual"
    anchors = _anchors(_timeline(job_id, backend, loud, words))
    clips = picker.snap_clips(picker.parse_plan(raw), words, src["duration"], opts["min_len"], opts["max_len"],
                              anchors=anchors)
    snapped = clips
    clips = _diversify(clips, int(opts["num_clips"]), _match_starts(job_id))
    if not clips:
        raise RuntimeError("nenhum candidato passou na revisão — tente outras instruções ou desligue a revisão visual")
    if opts.get("explore"):
        extra = _exploration([c for c in snapped if c not in clips], clips)
        if extra:
            clips.append({**extra, "explorar": True})
    markers = src.get("chapters") or []
    for c in clips:
        m = _marked(c, markers)
        if m:
            c["marcador"] = m
    for i, c in enumerate(clips, 1):
        c.update({"n": i, "status": "pending", "rev": 0, "review_image": images.get(c["candidate"])})
    if backend in ("cli", "api"):
        _stranger_test(job_id, backend, clips, words)
    update(job_id, clips=clips, status="rendering", progress=0.0)
    titles.record(JOBS_DIR, job_id, clips)
    _render_all(job_id, words)


def _exploration(rest: list[dict], picked: list[dict]) -> dict | None:
    """Um corte a mais, de propósito, do tipo que o canal menos mediu (e que não está entre os escolhidos):
    sem variedade o aprendizado só confirma o que já se posta. Nota mínima 50 para não virar enchimento."""
    kind = lambda c: (c.get("anchor") or {}).get("kind") or "sem_ancora"
    measured = titles.measured_counts(JOBS_DIR)
    have = {kind(c) for c in picked}
    pool = [c for c in rest if c.get("score", 0) >= 50 and not any(
        min(c["end"], p["end"]) - max(c["start"], p["start"]) > 0 for p in picked)]
    if not pool:
        return None
    return min(pool, key=lambda c: (kind(c) in have, measured.get(kind(c), 0), -c.get("score", 0)))


PER_MATCH = 2  # no teste do P0, 3 de 4 cortes saíram da mesma partida


def _match_starts(job_id: str) -> list[float]:
    """Começos de partida lidos no placar: 0x0 ou placar que diminui (partida nova)."""
    f = _dir(job_id) / "hud_events.json"
    if not f.exists():
        return []
    starts, prev = [], None
    for e in json.loads(f.read_text(encoding="utf-8")).get("events", []):
        try:
            sc = tuple(int(x) for x in (e.get("placar") or "").split(" x "))
        except ValueError:
            continue
        if len(sc) != 2:
            continue
        if e["tipo"] == "inicio" or prev is None or sc[0] < prev[0] or sc[1] < prev[1]:
            starts.append(e["t"] - 10)
        prev = sc
    return starts


def _diversify(clips: list[dict], want: int, match_starts: list[float]) -> list[dict]:
    """Os melhores cortes, com no máximo PER_MATCH por partida; se faltar opção em outras partidas, completa
    com o que sobrou (qualidade primeiro, variedade quando dá). `clips` já vem ordenado por nota."""
    if len(match_starts) < 2:
        return clips[:want]
    match_of = lambda t: sum(1 for s0 in match_starts if s0 <= t)
    picked, overflow, count = [], [], {}
    for c in clips:
        m = match_of(c.get("peak") or c["start"])
        if count.get(m, 0) < PER_MATCH:
            picked.append(c)
            count[m] = count.get(m, 0) + 1
        else:
            overflow.append(c)
        if len(picked) == want:
            break
    picked += overflow[: want - len(picked)]
    return sorted(picked, key=lambda c: c.get("score", 0), reverse=True)


def _stranger_test(job_id: str, backend: str, clips: list[dict], words: list[dict]) -> None:
    """Chamada cega (sem título nem contexto): quem nunca viu a live entende em 3 s? Reprovado fica com nota
    no máximo 60 — guarda barata contra o "corte sem contexto", a reclamação nº 1 de quem usa IA de cortes."""
    items, imgs = [], []
    for c in clips:
        img = JOBS_DIR / c["review_image"] if c.get("review_image") else None
        if not img or not img.exists():
            continue
        speech = " ".join(w["w"].strip() for w in words if c["start"] <= w["s"] < c["end"])
        items.append((c["n"], img.name, speech[:600]))
        imgs.append(img)
    if not items:
        return
    update(job_id, message="Conferindo se cada corte se entende sem contexto…")
    try:
        raw = picker.ask(backend, picker.STRANGER_SYSTEM, picker.stranger_prompt(items), picker.STRANGER_SCHEMA, imgs, step="teste_estranho")
    except Exception as exc:  # o teste é uma guarda: se falhar, os cortes seguem sem ele
        traceback.print_exc()
        update(job_id, message=f"Teste sem contexto indisponível ({str(exc)[:80]}); seguindo")
        return
    verdict = {v.get("n"): v for v in raw.get("clipes") or []}
    hud_file = _dir(job_id) / "hud_events.json"
    hud_data = json.loads(hud_file.read_text(encoding="utf-8")) if hud_file.exists() else {}
    for c in clips:
        v = verdict.get(c["n"])
        if not v:
            continue
        c["estranho"] = {"entende": bool(v.get("entende")), "motivo": (v.get("motivo") or "").strip()}
        if v.get("entende"):
            continue
        # o que mais falta a quem chega é o que estava em jogo: placar e relógio, lidos no HUD
        ctx = _context_text(c, hud_data.get("events", []), hud_data.get("trechos"))
        if ctx:
            c["contexto_tela"] = ctx
            c["overrides"] = {**c.get("overrides", {}), "title_overlay": True, "title_mode": "hook", "title_text": ctx}
        else:
            c["score"] = min(int(c.get("score", 0)), picker.STRANGER_CAP)


def _context_text(clip: dict, hud_events: list[dict], spans: list | None = None) -> str:
    """Texto de 3 s no começo do corte com o que estava em jogo, montado só com o que o placar mostrou:
    "3x3 · FALTAM 0:34", "4x4 · PRORROGAÇÃO". Vazio quando não há placar perto do lance."""
    peak = clip.get("peak") or clip["start"]
    if spans is None:
        return ""  # sem os trechos da partida não dá para garantir que o placar é desta partida
    span = next((sp for sp in spans if sp[0] <= peak <= sp[1]), None)
    if not span:
        return ""
    # um trecho com placar pode juntar partidas seguidas: recomeça a contagem em cada 0x0 ou placar menor
    before, prev = [], None
    for e in hud_events:
        if not e.get("placar") or not span[0] <= e["t"] <= peak + 1:
            continue
        try:
            sc = tuple(int(x) for x in e["placar"].split(" x "))
        except ValueError:
            continue
        if e["tipo"] == "inicio" or (prev and (sc[0] < prev[0] or sc[1] < prev[1])):
            before = []
        before.append(e)
        prev = sc
    if not before or peak - before[-1]["t"] > 330:  # sem placar recente desta partida: não arrisca
        return ""
    ev = before[-1]
    try:
        a, b = (int(x) for x in ev["placar"].split(" x "))
    except ValueError:
        return ""
    anchor = clip.get("anchor") or {}
    if anchor.get("kind") in ("gol_nosso", "gol_deles") and abs(anchor["t"] - ev["t"]) < 3:
        a, b = (a - 1, b) if anchor["kind"] == "gol_nosso" else (a, b - 1)  # o placar ANTES do gol
    clock = (ev.get("relogio") or "").strip()
    overtime = clock.startswith("+") or any(e["tipo"] == "fim_do_tempo" and ev["t"] - 400 < e["t"] <= ev["t"]
                                            for e in hud_events)
    if overtime:
        return f"{a}x{b} · PRORROGAÇÃO"
    # o relógio do jogo continua visível no corte (que começa ~10 s antes): número exato aqui conflitaria com ele
    try:
        mm, ss = (int(x) for x in clock.split(":"))
        left = mm * 60 + ss
    except ValueError:
        left = None
    if left is not None and left <= 60:
        return f"{a}x{b} · ÚLTIMO MINUTO"
    if left is not None and left <= 120:
        return f"{a}x{b} · RETA FINAL"
    return f"PLACAR {a}x{b}"


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


MOTION_Z = 3.0  # movimento do rosto bem acima do resto do trecho = reação


def _aim_reactions(job_id: str, clip: dict, zooms: list[float]) -> list[float]:
    """Mira o zoom no pico de movimento do rosto: ±2 s em volta da reação que o Claude viu, ou até 8 s
    depois do lance quando ele não marcou nenhuma. Sem rosto detectado, fica como estava."""
    job = get(job_id)
    src = job["source"]
    try:
        boxes = reframe.face_positions(Path(src["path"]), clip["start"], clip["end"] - clip["start"],
                                       src["width"], src["height"])
        face = reframe.face_box(boxes)
        if not face:
            return zooms
        out = []
        windows = [(z - 2, z + 2) for z in zooms] or (
            [(clip["peak"], min(clip["end"], clip["peak"] + 8))] if clip.get("peak") else [])
        for a, b in windows:
            a, b = max(clip["start"], a), min(clip["end"], b)
            hit = reframe.motion_peak(Path(src["path"]), a, b, src["width"], src["height"], face)
            if hit and hit[1] >= MOTION_Z:
                out.append(round(hit[0], 2))
            elif zooms:
                out.append(round((a + b) / 2, 2))  # rosto parado: mantém o segundo do Claude
        return out
    except Exception:
        traceback.print_exc()
        return zooms


def _edit_plan(job_id: str, clip: dict, opts: dict, words: list[dict]) -> tuple[list | None, list[float]]:
    """Jump cuts (pausas mortas) e momentos de zoom de um clipe."""
    loud_file = _dir(job_id) / "loudness.json"
    loud = json.loads(loud_file.read_text(encoding="utf-8")) if loud_file.exists() else []
    start, end = clip["start"], clip["end"]
    zooms: list[float] = []
    if opts.get("auto_zoom", True):
        zooms = [r for r in clip.get("reactions") or [] if start <= r <= end]
        zooms = _aim_reactions(job_id, clip, zooms)
        if not zooms and loud:  # sem dica do Claude nem rosto se mexendo: usa o maior pico de volume do trecho
            peaks = [e for e in signals.audio_events(loud, words)
                     if start <= e["t"] <= end and e["kind"] in signals.LOUD_KINDS and e["db"] >= 9]
            if peaks:
                zooms = [max(peaks, key=lambda e: e["db"])["t"]]
    keep = None
    if opts.get("remove_pauses") and loud:
        protect = [(z - 0.5, z + 2.5) for z in zooms]  # nunca corta dentro da reação
        if clip.get("peak"):
            protect.append((clip["peak"] - 1.5, clip["peak"] + 4.0))  # a pausa antes do lance é a tensão: fica
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
    if opts.get("pos_susto") and opts.get("pos_susto_musica"):
        opts["_pos_music"] = pos_susto_music(clip["n"])
    try:
        # legenda manual conta como fala: o corte de pausas não apaga o trecho dela
        keep, zooms = _edit_plan(job_id, clip, opts, captions.with_adds(words, opts.get("word_adds")))
        info = render.render_clip(Path(job["source"]["path"]), job["source"], clip, out_dir, words, opts,
                                  keep=keep, zooms=zooms)
        _set_clip(job_id, idx, file=f"{job_id}/clips/{clip['n']:02d}/clip.mp4",
                  thumb=f"{job_id}/clips/{clip['n']:02d}/thumb.jpg", cover_origin="render", **info)
        if opts.get("cover_auto", True) or clip.get("cover"):
            try:
                _make_cover(job_id, idx, words=words, attach=True)
            except Exception as exc:  # sem capa o clipe continua valendo
                traceback.print_exc()
                _set_clip(job_id, idx, cover_error=str(exc))
        _set_clip(job_id, idx, status="done", rev=clip.get("rev", 0) + 1)
    except Exception as exc:
        traceback.print_exc()
        _set_clip(job_id, idx, status="error", error=str(exc))


# ------------------------------------------------------------------ capa (thumbnail)

def _clip_dir(job_id: str, clip: dict) -> Path:
    return _dir(job_id) / "clips" / f"{clip['n']:02d}"


def _cover_frames(job_id: str, idx: int) -> tuple[list[Path], str]:
    """Quadros do clipe para a capa. Clipe renderizado antes da capa existir: tira agora."""
    job = get(job_id)
    clip = job["clips"][idx]
    d = _clip_dir(job_id, clip)
    found = capa.list_frames(d)
    if found:
        return found, clip.get("cover_origin") or "render"
    src = job["source"]
    vertical = src["width"] / src["height"] < 0.6
    if not vertical and not (d / "clip.mp4").exists():
        raise ValueError("renderize o clipe antes de fazer a capa")
    origin = capa.extract_frames(Path(src["path"]), clip["start"], clip["end"] - clip["start"], d, vertical)
    _set_clip(job_id, idx, cover_origin=origin)
    found = capa.list_frames(d)
    if not found:
        raise RuntimeError("não deu para tirar os quadros do clipe")
    return found, origin


def _apex_index(job_id: str, clip: dict, found: list[Path], origin: str, words: list[dict] | None) -> int:
    loud_file = _dir(job_id) / "loudness.json"
    events = []
    if loud_file.exists() and not clip.get("peak") and not clip.get("reactions"):
        words = words if words is not None else _words(job_id)
        events = [e for e in signals.audio_events(json.loads(loud_file.read_text(encoding="utf-8")), words)
                  if e["kind"] in signals.LOUD_KINDS]
    rel = capa.apex_time(clip, events) - clip["start"]
    if origin != "original" and clip.get("time_map"):  # quadros no tempo do vídeo final (com jump cuts)
        rel = edit.TimeMap([tuple(k) for k in clip["time_map"]]).to_out(rel)
    return capa.best_frame(found, rel)


def _cover_in_video(job: dict, clip: dict) -> bool:
    cover = clip.get("cover") or {}
    return bool(cover["in_video"]) if "in_video" in cover else bool(job["options"].get("cover_in_video"))


def _make_cover(job_id: str, idx: int, changes: dict | None = None, words: list[dict] | None = None,
                attach: bool = False) -> dict:
    """Gera ou atualiza a capa do clipe. Sem `changes`: automática (quadro do ápice + título),
    preservando o que o usuário já mudou à mão. `attach`: refaz a capa no fim do vídeo se ligada."""
    job = get(job_id)
    clip = job["clips"][idx]
    d = _clip_dir(job_id, clip)
    found, origin = _cover_frames(job_id, idx)
    clip = get(job_id)["clips"][idx]
    apex = _apex_index(job_id, clip, found, origin, words)
    old = clip.get("cover") or {}
    auto_text = capa.auto_highlight(clip["title"], clip.get("keywords"))
    if old:
        params = {k: old[k] for k in capa.PARAM_KEYS if k in old}
        if not old.get("custom_text"):
            params["text"] = auto_text
        if not old.get("custom_frame"):
            params["frame"] = apex
    else:
        params = capa.default_params(clip, apex, found[apex], job["options"])
    meta = {"custom_text": bool(old.get("custom_text")), "custom_frame": bool(old.get("custom_frame")),
            "in_video": bool(old.get("in_video", job["options"].get("cover_in_video")))}
    if changes:
        params = capa.clean_params(changes, params)
        meta["custom_text"] = params["text"].strip() != auto_text.strip()
        meta["custom_frame"] = params["frame"] != apex
        if "in_video" in changes:
            meta["in_video"] = bool(changes["in_video"])
    params["frame"] = min(params["frame"], len(found) - 1)
    out = capa.render(found[params["frame"]], params, d / "capa" / "capa.jpg")
    cover = {**params, **meta, "apex": apex, "frames": len(found), "auto_text": auto_text}
    _set_clip(job_id, idx, cover=cover, cover_error=None, cover_rev=clip.get("cover_rev", 0) + 1,
              cover_file=f"{job_id}/clips/{clip['n']:02d}/capa/{out.name}")
    if attach:
        _sync_cover_video(job_id, idx)
    return get(job_id)["clips"][idx]


def _sync_cover_video(job_id: str, idx: int) -> None:
    """Grava (ou tira) a capa do fim do vídeo conforme a opção."""
    job = get(job_id)
    clip = job["clips"][idx]
    d = _clip_dir(job_id, clip)
    if _cover_in_video(job, clip) and (d / "capa" / "capa.jpg").exists():
        capa.attach_to_video(d, render.encode, bool(job["source"].get("has_audio")), job["source"].get("fps") or 30)
    else:
        capa.detach_from_video(d)


def plain_clip(file: str) -> Path:
    """O clipe sem a capa gravada no fim (para compilar ou pós-editar)."""
    path = JOBS_DIR / file
    plain = path.with_name("sem_capa.mp4")
    return plain if plain.exists() else path


def cover_info(job_id: str, n: int) -> dict:
    job = get(job_id)
    if not job:
        raise KeyError(job_id)
    clip = job["clips"][_clip_index(job, n)]
    d = _clip_dir(job_id, clip)
    found = capa.list_frames(d)
    return {
        "cover": clip.get("cover"), "file": clip.get("cover_file"), "rev": clip.get("cover_rev", 0),
        "frames": [f"{job_id}/clips/{clip['n']:02d}/capa/quadros/{f.name}" for f in found],
        "step": capa.FRAME_STEP, "origin": clip.get("cover_origin") or ("render" if found else None),
        "in_video": _cover_in_video(job, clip), "title": clip["title"],
        "title_options": [o["title"] for o in clip.get("title_options") or [] if not o.get("problems")],
        "styles": {k: v["label"] for k, v in capa.STYLES.items()},
        "style_colors": {k: {"text": v["text"], "hl": v["hl"]} for k, v in capa.STYLES.items()},
        "handle": job["options"].get("cover_handle") or "",
    }


def save_cover(job_id: str, n: int, changes: dict) -> dict:
    """Editor da capa: salva, desenha e, se a capa vai no vídeo, regrava o fim do clipe (em segundo plano)."""
    job = get(job_id)
    if not job:
        raise KeyError(job_id)
    idx = _clip_index(job, n)
    clip = job["clips"][idx]
    if clip["status"] in ("pending", "rendering"):
        raise ValueError("espere a renderização do clipe terminar")
    before = _cover_in_video(job, clip)
    clip = _make_cover(job_id, idx, changes or None)
    after = _cover_in_video(get(job_id), clip)
    if before or after:
        _set_clip(job_id, idx, status="pending")

        def run() -> None:
            _set_clip(job_id, idx, status="rendering")
            try:
                _sync_cover_video(job_id, idx)
                _set_clip(job_id, idx, status="done", rev=get(job_id)["clips"][idx].get("rev", 0) + 1)
            except Exception as exc:
                traceback.print_exc()
                _set_clip(job_id, idx, status="done", cover_error=f"capa no vídeo: {exc}")
        _worker.submit(run)
    return get(job_id)["clips"][idx]


def preview_cover(job_id: str, n: int, changes: dict) -> Path:
    job = get(job_id)
    if not job:
        raise KeyError(job_id)
    idx = _clip_index(job, n)
    clip = job["clips"][idx]
    d = _clip_dir(job_id, clip)
    found = capa.list_frames(d)
    if not found or not clip.get("cover"):
        raise ValueError("a capa deste clipe ainda não foi gerada")
    params = capa.clean_params(changes, clip["cover"])
    frame = found[min(params["frame"], len(found) - 1)]
    out = d / "capa" / f"previa_{threading.get_ident()}.jpg"
    return capa.render(frame, params, out, preview=True)


def make_missing_covers(job_id: str) -> int:
    """Capas dos clipes prontos que ainda não têm (projetos de antes da capa existir)."""
    job = get(job_id)
    if not job:
        raise KeyError(job_id)
    if job["status"] not in ("done", "error"):
        raise ValueError("espere o processamento atual terminar")
    todo = [i for i, c in enumerate(job["clips"]) if c["status"] == "done" and not c.get("cover")]
    if not todo:
        raise ValueError("todos os clipes prontos já têm capa")
    update(job_id, status="rendering", progress=0.0, message="Fazendo as capas…")

    def run() -> None:
        words = _words(job_id)
        for k, idx in enumerate(todo):
            update(job_id, progress=k / len(todo), message=f"Fazendo capa {k + 1} de {len(todo)}…")
            try:
                _make_cover(job_id, idx, words=words, attach=True)
            except Exception as exc:
                traceback.print_exc()
                _set_clip(job_id, idx, cover_error=str(exc))
        update(job_id, status="done", progress=1.0, message=f"{len(todo)} capas prontas")
    _worker.submit(run)
    return len(todo)


def pos_susto_music(n: int) -> str | None:
    """Uma música de musicas/pos_susto da biblioteca (o usuário põe ali só o que tem direito de usar);
    clipes seguidos pegam músicas diferentes."""
    from . import posedit  # importado aqui: posedit importa jobs
    folder = posedit.library_dir() / "musicas" / "pos_susto"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    files = sorted(f for f in folder.iterdir() if f.suffix.lower() in posedit.AUDIO_EXT)
    return str(files[(n - 1) % len(files)]) if files else None


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


def create_clip(job_id: str, start: float, end: float, title: str = "", extra: dict | None = None,
                render: bool = True) -> dict:
    """Clipe feito à mão (busca na transcrição, linha do tempo, ou um momento que o Claude tinha descartado).
    No resumo da partida, o clipe novo é um lance: entra no short e não leva título gravado."""
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
    clip.update({k: v for k, v in (extra or {}).items() if k in ("peak", "reason", "visual", "score", "candidate")})
    if job["options"].get("mode") == "highlights":
        clip.update({"no_short": True, "tipo": "jogada", "narracao": [], "overrides": {"title_overlay": False}})
    with _lock:
        _jobs[job_id]["clips"].append(clip)
        _save(_jobs[job_id])
    if render:
        _worker.submit(_render_one, job_id, len(job["clips"]), words)
    return clip


def alternatives(job_id: str) -> list[dict]:
    """Momentos que o Claude analisou e não viraram corte: os da etapa 1 que não passaram, ou que passaram mas
    ficaram fora da quantidade pedida. Os que já se sobrepõem a um corte existente não aparecem."""
    job = get(job_id)
    if not job:
        raise KeyError(job_id)
    d = _dir(job_id)
    cand_file = d / "candidates.json"
    if not cand_file.exists():
        return []
    cands = json.loads(cand_file.read_text(encoding="utf-8")).get("clips", [])
    review_file = d / ("highlight-review.json" if job["options"].get("mode") == "highlights" else "review.json")
    reviewed = []
    if review_file.exists():
        data = json.loads(review_file.read_text(encoding="utf-8"))
        reviewed = [r for r in data.get("lances") or data.get("clips") or [] if "start" in r and "end" in r]
    # casa pelo TRECHO, não pelo número: no resumo a lista revisada mistura candidatos e gols injetados
    items = [(r, None) for r in reviewed]
    for c in cands:
        a, b = float(c.get("start", 0)), float(c.get("end", 0))
        if not any(min(b, float(r["end"])) - max(a, float(r["start"])) > 0.5 * (b - a) for r in reviewed):
            items.append((None, c))  # não chegou à revisão
    used = [(c["start"], c["end"]) for c in job["clips"]]
    out = []
    for k, (r, c) in enumerate(items, 1):
        src_ = r or c
        start, end = float(src_["start"]), float(src_["end"])
        if end - start < 2:
            continue
        overlap = max((min(end, b) - max(start, a) for a, b in used), default=0)
        if overlap > 0.5 * (end - start):
            continue
        situacao = ("descartado na revisão" if r and not r.get("keep", True) else
                    "ficou fora da quantidade pedida" if r else "não chegou à revisão")
        out.append({
            "id": f"m{k}-{int(start)}", "start": round(start, 2), "end": round(end, 2), "peak": src_.get("peak"),
            "title": (src_.get("label") or src_.get("title") or "").strip(), "visual": (src_.get("visual") or "").strip(),
            "reason": (src_.get("reason") or "").strip(), "score": int(src_.get("score") or 0), "situacao": situacao,
        })
    return sorted(out, key=lambda a: a["score"], reverse=True)


def use_alternative(job_id: str, alt_id: str) -> dict:
    alt = next((a for a in alternatives(job_id) if a["id"] == alt_id), None)
    if not alt:
        raise ValueError("esse momento não está mais disponível (talvez já tenha virado corte)")
    return create_clip(job_id, alt["start"], alt["end"], alt["title"],
                       extra={"peak": alt["peak"], "reason": f"Escolhido por você entre os momentos que o Claude descartou. {alt['reason']}",
                              "visual": alt["visual"], "score": alt["score"]})


def edit_recap(job_id: str, instruction: str) -> None:
    """Pedido em texto livre para mudar o resumo da partida: o Claude devolve operações, o Cortaí aplica e
    remonta os vídeos. Só texto (sem imagens): custa pouco."""
    job = get(job_id)
    if not job or job["options"].get("mode") != "highlights" or not job["clips"]:
        raise ValueError("isso vale para o resumo da partida")
    if job["status"] not in ("done", "error"):
        raise ValueError("espere o processamento terminar")
    if not (instruction or "").strip():
        raise ValueError("escreva o que mudar")
    backend = picker.resolve_backend(job.get("backend") or job["options"].get("backend", "auto"))
    if backend == "manual":
        raise ValueError("pedir mudanças precisa do Claude Code logado ou de ANTHROPIC_API_KEY")
    hl = dict(job.get("highlight") or {})
    hl["pedido"] = {"instrucao": instruction.strip(), "resposta": "Pensando…"}
    update(job_id, status="rendering", progress=0.0, error=None, message="Claude aplicando o seu pedido…", highlight=hl)
    _worker.submit(_guard, job_id, _edit_recap, instruction.strip(), backend)


def _edit_recap(job_id: str, instruction: str, backend: str) -> None:
    from . import highlights

    job = get(job_id)
    opts, src = job["options"], job["source"]
    start = float(opts.get("range_start") or 0)
    end = float(opts.get("range_end") or 0) or src["duration"]
    lances = sorted(job["clips"], key=lambda c: c["start"])
    f = _dir(job_id) / "hud_events.json"
    events = json.loads(f.read_text(encoding="utf-8")).get("events", []) if f.exists() else []
    raw = picker.ask(backend, highlights.EDIT_SYSTEM,
                     highlights.edit_prompt(instruction, lances, events, alternatives(job_id), start, end),
                     highlights.EDIT_SCHEMA, step="resumo_pedido")
    words = _words(job_id)
    by_n = {c["n"]: c for c in job["clips"]}
    to_render, removed, added = [], [], 0
    for op in raw.get("operacoes") or []:
        a, n = op.get("acao"), op.get("n")
        if a in ("remover", "short_sim", "short_nao", "ajustar") and n not in by_n:
            continue
        if a == "remover":
            removed.append(n)
        elif a in ("short_sim", "short_nao"):
            set_lance(job_id, n, a == "short_sim")
        elif a == "ajustar" and op.get("start") is not None and op.get("end") is not None:
            s0, e0 = max(start, float(op["start"])), min(end, float(op["end"]))
            if e0 - s0 >= 2:
                idx = _clip_index(get(job_id), n)
                _set_clip(job_id, idx, start=round(s0, 2), end=round(e0, 2), status="pending")
                to_render.append(idx)
        elif a == "adicionar" and op.get("start") is not None and op.get("end") is not None:
            s0, e0 = max(start, float(op["start"])), min(end, float(op["end"]))
            if e0 - s0 >= 2:
                c = create_clip(job_id, s0, e0, (op.get("titulo") or "").strip()[:60], render=False)
                to_render.append(_clip_index(get(job_id), c["n"]))
                added += 1
    # renderiza ANTES de remover: remover muda as posições na lista e o índice apontaria para outro lance
    for idx in sorted(set(to_render)):
        update(job_id, message="Renderizando os lances mudados…")
        _render_one(job_id, idx, words)
    for n in removed:
        delete_clip(job_id, n)
    hl = dict(get(job_id).get("highlight") or {})
    hl["pedido"] = {"instrucao": instruction, "resposta": (raw.get("resposta") or "").strip()}
    update(job_id, highlight=hl)
    _compile_highlights(job_id)


def match_preview(job_id: str) -> dict:
    """Prévia leve (H.264) só do trecho da partida, para o editor da partida: a live original costuma ser HEVC,
    que nem todo navegador toca. Gerada uma vez e guardada no projeto."""
    job = get(job_id)
    if not job:
        raise KeyError(job_id)
    src, opts = job["source"], job["options"]
    start = float(opts.get("range_start") or 0)
    end = float(opts.get("range_end") or 0) or src["duration"]
    name = f"partida_{int(start * 10)}_{int(end * 10)}.mp4"
    out = _dir(job_id) / name
    if not out.exists():
        render.proxy(Path(src["path"]), src, start, end, out)
    f = _dir(job_id) / "hud_events.json"
    events = json.loads(f.read_text(encoding="utf-8")).get("events", []) if f.exists() else []
    return {"file": f"{job_id}/{name}", "start": start, "end": end,
            "eventos": [{k: e.get(k) for k in ("t", "tipo", "placar")} for e in events
                        if start <= e["t"] <= end and e.get("tipo") in ("gol_nosso", "gol_deles", "fim_do_tempo")]}


def set_lance(job_id: str, n: int, no_short: bool) -> None:
    """Resumo da partida: o lance entra ou não no short (o horizontal usa todos). Vale ao remontar os vídeos."""
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        job["clips"][_clip_index(job, n)]["no_short"] = bool(no_short)
        _save(job)


FEEDBACK_REASONS = {
    "punchline": "cortou o punchline", "contexto": "sem contexto", "momento": "momento errado",
    "morto": "tempo morto", "outro": "outro motivo",
}
FEEDBACK_FILE = JOBS_DIR / "_feedback" / "feedback.csv"


def feedback(job_id: str, n: int, verdict: str, reason: str = "") -> dict:
    """Aprovar/rejeitar um corte com motivo. Fica no clipe e numa planilha com os sinais do corte — o rótulo
    mais rápido para calibrar a seleção (views chegam tarde e com ruído; a pesquisa recomenda isto antes)."""
    import csv

    if verdict not in ("ok", "rejeitado", ""):
        raise ValueError("veredito inválido")
    if verdict == "rejeitado" and reason not in FEEDBACK_REASONS:
        raise ValueError("escolha o motivo")
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        clip = job["clips"][_clip_index(job, n)]
        clip["feedback"] = {"veredito": verdict, "motivo": reason if verdict == "rejeitado" else "",
                            "quando": time.time()} if verdict else None
        _save(job)
        row = dict(clip)
    if verdict:
        FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        new = not FEEDBACK_FILE.exists()
        with open(FEEDBACK_FILE, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            if new:
                w.writerow(["quando", "job", "n", "veredito", "motivo", "score", "gancho", "ritmo", "graca",
                            "compartilhavel", "duracao", "ancora", "placar", "entende_sem_contexto", "titulo"])
            sc = row.get("scores") or {}
            anc = row.get("anchor") or {}
            w.writerow([time.strftime("%Y-%m-%d %H:%M"), job_id, n, verdict, FEEDBACK_REASONS.get(reason, ""),
                        row.get("score"), sc.get("hook"), sc.get("flow"), sc.get("value"), sc.get("trend"),
                        round(row["end"] - row["start"], 1), anc.get("kind", ""), anc.get("placar", ""),
                        (row.get("estranho") or {}).get("entende", ""), row.get("title", "")])
    return row.get("feedback") or {}


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
            if (clip_dir / "capa" / "capa.jpg").exists():
                z.write(clip_dir / "capa" / "capa.jpg", f"{base}-capa.jpg")
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
                     styles.LEARN_SCHEMA, images, step="estilo_aprender")
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
