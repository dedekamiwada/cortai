"""Ingestão de vídeo: download por URL (yt-dlp), probe (ffprobe) e extração de áudio."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from . import paths


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-1500:]
        raise RuntimeError(f"{Path(cmd[0]).name} falhou ({proc.returncode}): {tail}")
    return proc


def download(url: str, dest_dir: Path, max_height: int = 1080) -> tuple[Path, str]:
    """Baixa o vídeo como source.mp4 com o yt-dlp. Devolve (arquivo, título)."""
    out = dest_dir / "source.%(ext)s"
    proc = run([
        *paths.yt_dlp_command(),
        "-f", f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
        "--merge-output-format", "mp4",
        "--no-playlist", "--no-progress", "--no-simulate", "--print", "after_move:title",
        "-o", str(out), url,
    ])
    files = sorted(p for p in dest_dir.glob("source.*") if p.suffix != ".part")
    if not files:
        raise RuntimeError("yt-dlp terminou mas nenhum arquivo foi gerado")
    return files[0], proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""


def probe(path: Path) -> dict:
    proc = run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", str(path),
    ])
    info = json.loads(proc.stdout)
    video = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError("o arquivo não tem faixa de vídeo")
    has_audio = any(s.get("codec_type") == "audio" for s in info["streams"])
    num, _, den = (video.get("avg_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    width, height = int(video["width"]), int(video["height"])
    rotation = 0
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = abs(int(side["rotation"])) % 180
    if rotation == 90:
        width, height = height, width
    return {
        "duration": float(info["format"]["duration"]),
        "width": width,
        "height": height,
        "fps": fps or 30.0,
        "has_audio": has_audio,
    }


def probe_any(path: Path) -> dict:
    """Como `probe`, mas aceita arquivos só de áudio (música, narração)."""
    proc = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)])
    info = json.loads(proc.stdout)
    has_video = any(s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1
                    for s in info["streams"])
    if has_video:
        return probe(path)
    if not any(s.get("codec_type") == "audio" for s in info["streams"]):
        raise RuntimeError("o arquivo não tem vídeo nem áudio")
    return {"duration": float(info["format"]["duration"]), "width": 0, "height": 0, "fps": 0, "has_audio": True}


def extract_audio(src: Path, dest: Path, duration: float = 0.0,
                  on_progress: Callable[[float], None] = lambda p: None) -> Path:
    """Extrai WAV 16 kHz mono, informando o progresso pelo `-progress` do ffmpeg."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", str(src),
           "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dest)]
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True, encoding="utf-8", errors="replace")
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and duration and value.isdigit():
                on_progress(min(int(value) / 1e6 / duration, 1.0))
        if proc.wait() != 0:
            err.seek(0)
            tail = err.read().decode("utf-8", "replace").strip()[-1500:]
            raise RuntimeError(f"ffmpeg falhou ao extrair o áudio ({proc.returncode}): {tail}")
    return dest
