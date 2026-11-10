"""Folhas de frames: uma imagem em grade com N quadros de um trecho, cada um com o segundo
do vídeo original escrito em cima. É assim que o Claude "assiste" a um candidato."""
from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np


def sample(src: Path, start: float, end: float, n: int, width: int,
           src_w: int, src_h: int) -> tuple[list[np.ndarray], list[float]]:
    """Decodifica o trecho uma vez (mais rápido que N buscas) e devolve até n quadros BGR."""
    dur = max(0.5, end - start)
    rate = n / dur
    height = int(round(src_h * width / src_w / 2)) * 2
    cmd = [
        "ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
        "-vf", f"fps={rate:.5f},scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
    ]
    data = subprocess.run(cmd, capture_output=True).stdout
    size = width * height * 3
    count = min(n, len(data) // size)
    frames = [np.frombuffer(data[i * size : (i + 1) * size], np.uint8).reshape(height, width, 3)
              for i in range(count)]
    times = [start + (i + 0.5) / rate for i in range(count)]
    return frames, times


def grid(src: Path, start: float, end: float, out: Path, src_w: int, src_h: int,
         n: int = 12, cols: int = 4) -> Path:
    vertical = src_w / src_h < 1
    width = 270 if vertical else 400
    frames, times = sample(src, start, end, n, width, src_w, src_h)
    if not frames:
        raise RuntimeError(f"não consegui extrair quadros de {start:.1f}s a {end:.1f}s")
    h, w = frames[0].shape[:2]
    rows = (len(frames) + cols - 1) // cols
    sheet = np.zeros((rows * h, cols * w, 3), np.uint8)
    for i, (frame, t) in enumerate(zip(frames, times)):
        r, c = divmod(i, cols)
        tile = frame.copy()
        label = f"{t:.1f}s"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        cv2.rectangle(tile, (0, 0), (tw + 10, th + 12), (0, 0, 0), -1)
        cv2.putText(tile, label, (5, th + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 230, 255), 2, cv2.LINE_AA)
        cv2.rectangle(tile, (0, 0), (w - 1, h - 1), (60, 60, 60), 1)
        sheet[r * h : (r + 1) * h, c * w : (c + 1) * w] = tile
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), sheet, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return out
