"""API HTTP + interface web (somente localhost)."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import captions, jobs, picker, styles

STATIC = Path(__file__).resolve().parent.parent / "static"

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
        "defaults": jobs.DEFAULT_OPTS,
        "caption_presets": {k: v["label"] for k, v in captions.PRESETS.items()},
    }


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


app.mount("/media", StaticFiles(directory=jobs.JOBS_DIR), name="media")
app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
