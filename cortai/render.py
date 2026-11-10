"""Renderização de um clipe vertical 1080x1920 com ffmpeg.

Layouts:
  face   — corte 9:16 seguindo o rosto (vídeo horizontal)
  manual — corte 9:16 fixo na posição escolhida no editor (crop_x 0..1)
  split  — tela dividida: webcam em cima, jogo embaixo (vídeo horizontal com rosto)
  fit    — vídeo inteiro no meio com fundo desfocado
  auto   — face se houver rosto em ≥35% do trecho, senão fit
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import captions, reframe

OUT_W, OUT_H = 1080, 1920
SPLIT_TOP = 768  # altura do painel da webcam; o jogo fica com 1152
_encoder: list[str] | None = None


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def _encoders() -> list[list[str]]:
    nvenc = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"]
    x264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    return [_encoder] if _encoder else [nvenc, x264]


def _run_encode(args_before_enc: list[str], args_after_enc: list[str], cwd: Path) -> None:
    global _encoder
    error = ""
    for enc in _encoders():
        proc = subprocess.run(args_before_enc + enc + args_after_enc, cwd=cwd, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        if proc.returncode == 0:
            _encoder = enc
            return
        error = proc.stderr[-1500:]
    raise RuntimeError(f"ffmpeg falhou ao renderizar: {error}")


def _fit_filter(subs: str) -> str:
    return (
        f"[0:v]split=2[a][b];"
        f"[a]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
        f"gblur=sigma=30,eq=brightness=-0.08[bg];"
        f"[b]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1{subs}[v]"
    )


def render_clip(src: Path, meta: dict, clip: dict, out_dir: Path, words: list[dict], opts: dict) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = float(clip["start"]), float(clip["end"])
    dur = end - start
    w, h = meta["width"], meta["height"]
    is_landscape = w / h > 0.6
    layout = opts.get("layout") or "auto"
    crop_x = min(1.0, max(0.0, float(opts.get("crop_x", 0.5))))
    if not is_landscape:
        layout = "fit"  # vídeo vertical já está no formato certo

    boxes: list = []
    face_ratio = 0.0
    if layout in ("auto", "face", "split"):
        boxes = reframe.face_positions(src, start, dur, w, h)
        face_ratio = sum(b is not None for b in boxes) / len(boxes) if boxes else 0.0
        if layout == "auto":
            layout = "face" if face_ratio >= 0.35 else "fit"
        if layout == "split" and not reframe.face_box(boxes):
            layout = "fit"  # sem rosto não há webcam para o painel de cima

    subs = ""
    if (opts.get("captions", True) and words) or opts.get("title_overlay"):
        (out_dir / "subs.ass").write_text(captions.build_ass(words, start, end, opts), encoding="utf-8")
        subs = ",subtitles=subs.ass"

    crop_w, crop_h = _even(h * 9 / 16), _even(h)
    if layout == "face":
        path, _ = reframe.camera_path(boxes)
        script, x0 = reframe.sendcmd_script(path, w, crop_w, dur)
        (out_dir / "cmds.txt").write_text(script, encoding="utf-8")
        send = "sendcmd=f=cmds.txt," if script else ""
        vf = f"[0:v]{send}crop={crop_w}:{crop_h}:{x0}:0,scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1{subs}[v]"
    elif layout == "manual":
        x0 = int(min(max(crop_x * w - crop_w / 2, 0), w - crop_w))
        vf = f"[0:v]crop={crop_w}:{crop_h}:{x0}:0,scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1{subs}[v]"
    elif layout == "split":
        cx, cy, fw = reframe.face_box(boxes)
        top_aspect = OUT_W / SPLIT_TOP
        cam_w = min(max(fw * w * 4.0, w * 0.18), w * 0.6, h * top_aspect)
        cam_h = cam_w / top_aspect
        cam_w, cam_h = _even(cam_w), _even(cam_h)
        cam_x = int(min(max(cx * w - cam_w / 2, 0), w - cam_w))
        cam_y = int(min(max(cy * h - cam_h * 0.45, 0), h - cam_h))
        game_h = OUT_H - SPLIT_TOP
        game_w = _even(min(w, h * OUT_W / game_h))
        game_x = int(min(max(crop_x * w - game_w / 2, 0), w - game_w))
        vf = (
            f"[0:v]split=2[c][g];"
            f"[c]crop={cam_w}:{cam_h}:{cam_x}:{cam_y},scale={OUT_W}:{SPLIT_TOP}:flags=lanczos[top];"
            f"[g]crop={game_w}:{_even(h)}:{game_x}:0,scale={OUT_W}:{game_h}:flags=lanczos[bot];"
            f"[top][bot]vstack,setsar=1{subs}[v]"
        )
    else:
        layout = "fit"
        vf = _fit_filter(subs)

    base = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
            "-filter_complex", vf, "-map", "[v]"]
    audio = ["-map", "0:a:0", "-c:a", "aac", "-b:a", "160k"] if meta.get("has_audio") else []
    tail = ["-r", f"{min(meta.get('fps') or 30, 60):.3f}", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "clip.mp4"]
    _run_encode(base, audio + tail, out_dir)

    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(1.0, dur / 2):.2f}", "-i", "clip.mp4",
                    "-frames:v", "1", "-vf", "scale=360:-2", "thumb.jpg"], cwd=out_dir, capture_output=True)
    return {"layout": layout, "face_ratio": round(face_ratio, 2)}


def proxy(src: Path, meta: dict, start: float, end: float, out: Path) -> Path:
    """Prévia leve (baixa resolução) de um trecho, para o editor tocar no navegador."""
    out.parent.mkdir(parents=True, exist_ok=True)
    vertical = meta["width"] / meta["height"] < 1
    scale = "scale=-2:640" if vertical else "scale=640:-2"
    base = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}",
            "-vf", scale, "-map", "0:v:0"]
    audio = ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "96k"]
    tail = ["-pix_fmt", "yuv420p", "-g", "15", "-movflags", "+faststart", out.name]
    _run_encode(base, audio + tail, out.parent)
    return out
