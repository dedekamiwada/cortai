"""Folhas de frames: uma imagem em grade com N quadros de um trecho, cada um com o segundo
do vídeo original escrito em cima. É assim que o Claude "assiste" a um candidato."""
from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np


# Amostragem espaçada (mapa de partidas, varredura) não precisa de todos os quadros: decodificar só os
# keyframes (OBS grava 1 a cada 2 s) com a GPU foi 23x mais rápido numa live HEVC 1080x1920
# (10 min: 25,8 s → 1,1 s). Abaixo deste intervalo entre quadros, decodifica tudo para não perder precisão.
KEYFRAME_ONLY_STEP = 4.0
_cuda_ok: bool | None = None


def sample(src: Path, start: float, end: float, n: int, width: int,
           src_w: int, src_h: int, fast: bool = False) -> tuple[list[np.ndarray], list[float]]:
    """Decodifica o trecho uma vez (mais rápido que N buscas) e devolve até n quadros BGR."""
    global _cuda_ok
    dur = max(0.5, end - start)
    rate = n / dur
    height = int(round(src_h * width / src_w / 2)) * 2
    tail = ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
            "-vf", f"fps={rate:.5f},scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    size = width * height * 3
    data = b""
    if fast and 1 / rate >= KEYFRAME_ONLY_STEP:  # só varredura: o segundo de cada quadro fica aproximado
        fast = ["-skip_frame", "nokey"]
        if _cuda_ok is not False:
            proc = subprocess.run(["ffmpeg", "-v", "error", "-hwaccel", "cuda", *fast, *tail], capture_output=True)
            data = proc.stdout
            if proc.returncode != 0:
                _cuda_ok = False  # sem GPU NVIDIA/driver: não tenta de novo nesta sessão
            elif data:
                _cuda_ok = True
        if len(data) < size:
            data = subprocess.run(["ffmpeg", "-v", "error", *fast, *tail], capture_output=True).stdout
    if len(data) < size:
        data = subprocess.run(["ffmpeg", "-v", "error", *tail], capture_output=True).stdout
    count = min(n, len(data) // size)
    frames = [np.frombuffer(data[i * size : (i + 1) * size], np.uint8).reshape(height, width, 3)
              for i in range(count)]
    times = [start + (i + 0.5) / rate for i in range(count)]
    return frames, times


FOCUS_SPAN = 2.0  # s em volta do pico que ganham metade dos quadros


def grid(src: Path, start: float, end: float, out: Path, src_w: int, src_h: int,
         n: int = 12, cols: int = 4, focus: float | None = None, fast: bool = False) -> Path:
    """Folha de n quadros. Com `focus` (o pico do lance), metade dos quadros cobre o trecho todo e a outra
    metade fica em ±2 s do pico — o Claude julga o lance que importa, não o entorno."""
    vertical = src_w / src_h < 1
    width = 270 if vertical else 400
    if focus is not None and start < focus < end:
        f1, t1 = sample(src, start, end, n - n // 2, width, src_w, src_h)
        f2, t2 = sample(src, max(start, focus - FOCUS_SPAN), min(end, focus + FOCUS_SPAN), n // 2, width, src_w, src_h)
        pairs = sorted(zip(t1 + t2, f1 + f2), key=lambda p: p[0])
        times, frames = [p[0] for p in pairs], [p[1] for p in pairs]
    else:
        frames, times = sample(src, start, end, n, width, src_w, src_h, fast)
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
    # cv2.imwrite não aceita caminho com acento no Windows (falha calada); codifica e grava pelo Python
    ok, buf = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        raise RuntimeError("não consegui gerar a folha de frames")
    out.write_bytes(buf.tobytes())
    return out
