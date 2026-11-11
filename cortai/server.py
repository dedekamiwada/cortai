"""API HTTP + interface web (somente localhost)."""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import autocaptions, captions, fx, jobs, paths, picker, studio, studio_render, styles

app = FastAPI(title="Cortaí")
jobs.load_existing()


def _opts(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "options inválido")
    allowed = set(jobs.DEFAULT_OPTS)
    opts = {k: v for k, v in data.items() if k in allowed}
    for key in ("num_clips", "min_len", "max_len"):
        if key in opts:
            opts[key] = int(opts[key])
    for key in ("range_start", "range_end"):
        if key in opts:
            opts[key] = float(opts[key] or 0)
    if opts.get("min_len", 1) >= opts.get("max_len", 10**6):
        raise HTTPException(400, "duração mínima precisa ser menor que a máxima")
    return opts


@app.get("/api/config")
def config():
    return {
        "api": picker.api_available(),
        "cli": picker.cli_status(),
        "model": picker.MODEL,
        "defaults": jobs.defaults(),
        "caption_presets": {k: v["label"] for k, v in captions.PRESETS.items()},
    }


@app.post("/api/settings")
async def save_settings(request: Request):
    return jobs.save_defaults(await request.json())


@app.post("/api/jobs/{job_id}/apply")
async def apply_all(job_id: str, request: Request):
    _job_or_404(job_id)
    body = await request.json()
    try:
        count = jobs.apply_all(job_id, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if body.get("save_default"):
        jobs.save_defaults(body)
    return {"ok": True, "clips": count}


@app.post("/api/jobs/{job_id}/clips")
async def create_clip(job_id: str, request: Request):
    _job_or_404(job_id)
    body = await request.json()
    try:
        return jobs.create_clip(job_id, body.get("start", 0), body.get("end", 0), body.get("title", ""))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/jobs/{job_id}/clips/{n}")
def delete_clip(job_id: str, n: int):
    _job_or_404(job_id)
    try:
        jobs.delete_clip(job_id, n)
    except KeyError:
        raise HTTPException(404, "clipe não encontrado")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.get("/api/jobs/{job_id}/export.zip")
def export_zip(job_id: str):
    job = _job_or_404(job_id)
    try:
        path = jobs.export_zip(job_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    name = "".join(ch for ch in Path(job["name"]).stem if ch.isalnum() or ch in " -_")[:40] or "cortes"
    return FileResponse(path, media_type="application/zip", filename=f"{name}-cortes.zip")


@app.get("/api/styles")
def list_styles():
    return styles.list_styles(jobs.JOBS_DIR)


@app.post("/api/styles")
async def create_style(request: Request):
    body = await request.json()
    urls = body.get("urls") or []
    if isinstance(urls, str):
        urls = urls.split()
    try:
        return jobs.create_style(str(body.get("name", "")), urls, str(body.get("notes", "")))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/styles/{style_id}")
def delete_style(style_id: str):
    try:
        styles.delete(jobs.JOBS_DIR, style_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.get("/api/jobs/{job_id}/clips/{n}/editor")
def clip_editor(job_id: str, n: int):
    _job_or_404(job_id)
    try:
        return jobs.editor_data(job_id, n)
    except KeyError:
        raise HTTPException(404, "clipe não encontrado")


@app.get("/api/jobs")
def list_jobs():
    return [
        {k: j[k] for k in ("id", "name", "created", "status", "progress", "message", "error")}
        | {"clips": len(j["clips"]), "thumb": next((c.get("thumb") for c in j["clips"] if c.get("thumb")), None)}
        for j in jobs.list_jobs()
    ]


UPLOADS = jobs.JOBS_DIR / "_uploads"
UPLOADS.mkdir(exist_ok=True)
_WRITE_BUFFER = 8 * 1024 * 1024


@app.post("/api/uploads")
async def upload(request: Request, filename: str = "video.mp4"):
    """Recebe o vídeo cru no corpo da requisição e grava direto no disco, uma única vez.

    A escrita roda em thread, em blocos de 8 MB, para o servidor continuar respondendo
    durante uploads de vários GB.
    """
    suffix = Path(filename).suffix.lower() or ".mp4"
    dest = UPLOADS / f"{uuid.uuid4().hex}{suffix}"
    buf = bytearray()
    try:
        with open(dest, "wb") as f:
            async for chunk in request.stream():
                buf += chunk
                if len(buf) >= _WRITE_BUFFER:
                    await run_in_threadpool(f.write, bytes(buf))
                    buf.clear()
            if buf:
                await run_in_threadpool(f.write, bytes(buf))
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    if dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "arquivo vazio")
    return {"upload_id": dest.name}


@app.post("/api/jobs")
def create_job(
    options: str = Form("{}"),
    url: str = Form(""),
    local_path: str = Form(""),
    upload_id: str = Form(""),
    name: str = Form(""),
):
    opts = _opts(options)
    upload_path = None
    if upload_id:
        upload_path = UPLOADS / Path(upload_id).name  # só o nome: nada de sair da pasta
        if not upload_path.is_file():
            raise HTTPException(400, "upload não encontrado")
    try:
        job = jobs.create(name, opts, url=url or None, local_path=local_path or None, upload_path=upload_path)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc))
    return {"id": job["id"]}


def _job_or_404(job_id: str) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job não encontrado")
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return _job_or_404(job_id)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    _job_or_404(job_id)
    jobs.delete(job_id)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/prompt", response_class=PlainTextResponse)
def get_prompt(job_id: str):
    _job_or_404(job_id)
    return jobs.prompt_text(job_id)


@app.get("/api/jobs/{job_id}/transcript")
def get_transcript(job_id: str):
    _job_or_404(job_id)
    return jobs.transcript(job_id) or {"segments": []}


@app.post("/api/jobs/{job_id}/manual")
async def submit_manual(job_id: str, request: Request):
    _job_or_404(job_id)
    raw = (await request.body()).decode("utf-8", errors="replace")
    try:
        jobs.submit_manual(job_id, raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.post("/api/jobs/{job_id}/reselect")
async def reselect(job_id: str, request: Request):
    _job_or_404(job_id)
    body = await request.json()
    try:
        jobs.reselect(job_id, _opts(json.dumps(body)))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.post("/api/jobs/{job_id}/clips/{n}")
async def rerender(job_id: str, n: int, request: Request):
    _job_or_404(job_id)
    try:
        jobs.rerender(job_id, n, await request.json())
    except KeyError:
        raise HTTPException(404, "clipe não encontrado")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.get("/api/jobs/{job_id}/source")
def source_video(job_id: str):
    job = _job_or_404(job_id)
    path = job["source"].get("path")
    if not path or not Path(path).is_file():
        raise HTTPException(404, "vídeo original indisponível")
    return FileResponse(path)


# ------------------------------------------------------------------ Estúdio (editor sem IA)

studio.init(jobs.JOBS_DIR)


def _project_or_404(pid: str) -> dict:
    try:
        return studio.load(pid)
    except (KeyError, FileNotFoundError):
        raise HTTPException(404, "projeto não encontrado")


@app.get("/api/studio/projects")
def studio_projects():
    return studio.list_projects()


@app.post("/api/studio/projects")
async def studio_create(request: Request):
    body = await request.json()
    p = studio.create(str(body.get("name", "")), str(body.get("ratio", "9:16")))
    for path in body.get("paths") or []:
        try:
            studio.add_asset(p["id"], path)
        except FileNotFoundError:
            pass
    if body.get("from_clip"):  # abrir um clipe do Cortaí no editor
        fc = body["from_clip"]
        job = jobs.get(fc.get("job", ""))
        if job:
            clip = next((c for c in job["clips"] if c["n"] == fc.get("n")), None)
            if clip:
                # recorta só o trecho do clipe (+10 s de folga de cada lado) em vez de usar a live inteira
                margin = 10.0
                seg_start = max(0.0, clip["start"] - margin)
                seg_end = min(job["source"]["duration"], clip["end"] + margin)
                seg = studio._pdir(p["id"]) / "uploads" / f"clipe-{clip['n']:02d}.mp4"
                seg.parent.mkdir(exist_ok=True)
                await run_in_threadpool(_cut_segment, job["source"]["path"], seg_start, seg_end, seg)
                asset = studio.add_asset(p["id"], seg)
                # leva junto o que o clipe tem: cortes de pausa, zooms de reação, legendas e título
                tracks = await run_in_threadpool(jobs.studio_tracks, job["id"], clip["n"], seg_start, asset["id"])

                def fill(pr, tracks=tracks):
                    pr["tracks"]["main"] = tracks["main"]
                    pr["tracks"]["text"] = tracks["text"]
                studio.mutate(p["id"], fill)
    return studio.load(p["id"])


def _cut_segment(src: str, start: float, end: float, out: Path) -> None:
    base = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", src, "-t", f"{end - start:.3f}",
            "-map", "0:v:0", "-map", "0:a:0?"]
    for enc in (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "17"], ["-c:v", "libx264", "-preset", "fast", "-crf", "17"]):
        if subprocess.run(base + enc + ["-c:a", "aac", "-b:a", "192k", str(out)], capture_output=True).returncode == 0:
            return
    raise HTTPException(500, "não consegui recortar o trecho do clipe")


@app.get("/api/studio/projects/{pid}")
def studio_get(pid: str):
    return _project_or_404(pid)


@app.put("/api/studio/projects/{pid}")
async def studio_save(pid: str, request: Request):
    _project_or_404(pid)
    return studio.save(pid, await request.json())


@app.delete("/api/studio/projects/{pid}")
def studio_delete(pid: str):
    _project_or_404(pid)
    studio.delete(pid)
    return {"ok": True}


@app.post("/api/studio/projects/{pid}/assets")
async def studio_add_asset(pid: str, request: Request):
    _project_or_404(pid)
    body = await request.json()
    path = body.get("path")
    if body.get("upload_id"):
        path = UPLOADS / Path(body["upload_id"]).name
        if not path.is_file():
            raise HTTPException(400, "upload não encontrado")
        dest = studio._pdir(pid) / "uploads"
        dest.mkdir(exist_ok=True)
        target = dest / (Path(str(body.get("name") or path.name)).name or path.name)
        path.replace(target)
        path = target
    try:
        return studio.add_asset(pid, path)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/studio/projects/{pid}/autocaptions")
async def studio_autocaptions(pid: str, request: Request):
    """Legenda automática: transcreve a faixa principal em segundo plano (ver autocaptions.py)."""
    _project_or_404(pid)
    try:
        autocaptions.start(pid, await request.json())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.post("/api/studio/projects/{pid}/autocaptions/ack")
def studio_autocaptions_ack(pid: str):
    """O editor já colocou as legendas na timeline: limpa o resultado guardado."""
    _project_or_404(pid)
    studio.mutate(pid, lambda p: p.__setitem__("captions_job", {**(p.get("captions_job") or {}), "items": None,
                                                                 "status": "applied"}))
    return {"ok": True}


@app.get("/api/studio/fx")
def studio_fx():
    """Filtros de cor (com as matrizes, para a prévia), efeitos e efeitos de voz."""
    return fx.catalog()


@app.post("/api/studio/projects/{pid}/audio-preview")
async def studio_audio_preview(pid: str, request: Request):
    """Ouve 6 s do áudio de um item com a limpeza/efeito de voz (o navegador não faz esses efeitos)."""
    p = _project_or_404(pid)
    body = await request.json()
    track = str(body.get("track") or "")
    it = next((x for x in p["tracks"].get(track, []) if x.get("id") == body.get("id")), None)
    a = p["assets"].get(it.get("asset")) if it else None
    if not it or not a or not a.get("has_audio"):
        raise HTTPException(400, "esse item não tem áudio")
    try:
        src = max(0.0, float(body.get("src") or 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "tempo inválido") from None
    out_dir = studio._pdir(pid) / "exports"
    out_dir.mkdir(exist_ok=True)
    name = f"previa-voz-{it['id']}.m4a"
    vol = float(it.get("volume", 0.0 if track == "overlay" else 1.0) or 0) or 1.0
    chain = [f"volume={vol:.3f}", *fx.voice_chain(it), studio_render.AUDIO_FMT]

    def run() -> subprocess.CompletedProcess:
        return subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{src:.3f}", "-t", "6", "-i", a["path"], "-vn",
                               "-af", ",".join(chain), "-c:a", "aac", "-b:a", "160k", name],
                              cwd=out_dir, capture_output=True, text=True, encoding="utf-8", errors="replace")

    r = await run_in_threadpool(run)
    if r.returncode != 0:
        raise HTTPException(500, (r.stderr or "ffmpeg falhou").strip()[-400:])
    return {"url": f"/media/_studio/{pid}/exports/{name}?v={uuid.uuid4().hex[:6]}"}


@app.post("/api/studio/projects/{pid}/freeze")
async def studio_freeze(pid: str, request: Request):
    """Congelar quadro: salva o quadro do vídeo no tempo pedido como imagem do projeto."""
    p = _project_or_404(pid)
    body = await request.json()
    a = p["assets"].get(str(body.get("asset") or ""))
    if not a or a.get("kind") != "video":
        raise HTTPException(400, "escolha um vídeo da faixa principal")
    try:
        t = max(0.0, float(body.get("t") or 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "tempo inválido") from None
    dest = studio._pdir(pid) / "uploads"
    dest.mkdir(exist_ok=True)
    stem = "".join(ch for ch in Path(a["name"]).stem if ch.isalnum() or ch in " -_")[:40].strip() or "video"
    out = dest / f"congelado-{stem}-{t:.2f}s.png"

    def grab() -> None:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", a["path"], "-frames:v", "1",
                        "-update", "1", str(out)], capture_output=True)
        if not out.is_file():  # perto do fim do arquivo: pega o último quadro disponível
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.2", "-i", a["path"], "-update", "1",
                            str(out)], capture_output=True)

    await run_in_threadpool(grab)
    if not out.is_file():
        raise HTTPException(500, "não consegui extrair o quadro desse ponto do vídeo")
    return studio.add_asset(pid, out)


@app.delete("/api/studio/projects/{pid}/assets/{aid}")
def studio_delete_asset(pid: str, aid: str):
    def drop(p):
        if any(it.get("asset") == aid for tr in p["tracks"].values() for it in tr):
            raise ValueError("a mídia está em uso na timeline")
        p["assets"].pop(aid, None)
    _project_or_404(pid)
    try:
        studio.mutate(pid, drop)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.post("/api/studio/projects/{pid}/export")
async def studio_export(pid: str, request: Request):
    p = _project_or_404(pid)
    if (p.get("export") or {}).get("status") == "running":
        raise HTTPException(400, "já existe uma exportação em andamento")
    if any(a.get("status") != "ready" for a in p["assets"].values()
           if any(it.get("asset") == a["id"] for tr in p["tracks"].values() for it in tr)):
        raise HTTPException(400, "espere as mídias da timeline terminarem de carregar")
    body = await request.json()
    studio.mutate(pid, lambda pr: pr.__setitem__("export", {"status": "running", "progress": 0.0}))
    studio._worker.submit(studio_render.export, pid, body)
    return {"ok": True}


app.mount("/media", StaticFiles(directory=jobs.JOBS_DIR), name="media")
app.mount("/", StaticFiles(directory=paths.STATIC, html=True), name="static")
