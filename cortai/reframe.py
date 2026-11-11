"""Reenquadramento 16:9 → 9:16 seguindo o rosto (OpenCV), com câmera suavizada."""
from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

SAMPLE_FPS = 5


def _load_cascade() -> "cv2.CascadeClassifier":
    """O OpenCV no Windows não abre caminhos com acento (ex.: D:\\EdiçãoClaude). Copia o XML do
    detector para a pasta temporária, que costuma ter caminho sem acento, antes de carregar."""
    import os
    import shutil
    import tempfile
    xml = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    cascade = cv2.CascadeClassifier(xml)
    if not cascade.empty():
        return cascade
    for folder in (tempfile.gettempdir(), os.environ.get("PUBLIC", ""), "C:\\"):
        if not folder:
            continue
        try:
            dst = os.path.join(folder, "cortai_haarcascade_frontalface_default.xml")
            shutil.copyfile(xml, dst)
            cascade = cv2.CascadeClassifier(dst)
            if not cascade.empty():
                return cascade
        except OSError:
            pass
    return cascade  # vazio: face_positions devolve "sem rosto" em vez de travar o programa


_cascade = _load_cascade()


def face_positions(src: Path, start: float, dur: float, src_w: int, src_h: int) -> list[tuple[float, float, float] | None]:
    """Maior rosto de cada amostra como (centro x, centro y, largura), normalizados 0..1, ou None."""
    sw = 480
    sh = max(2, int(round(src_h * sw / src_w / 2)) * 2)
    cmd = [
        "ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
        "-vf", f"fps={SAMPLE_FPS},scale={sw}:{sh}", "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    data = subprocess.run(cmd, capture_output=True).stdout
    size = sw * sh
    n = len(data) // size
    if n == 0:
        return []
    frames = np.frombuffer(data[: n * size], np.uint8).reshape(n, sh, sw)
    min_face = max(16, int(sh * 0.08))
    out: list[tuple[float, float, float] | None] = []
    if _cascade.empty():
        return [None] * n
    for frame in frames:
        faces = _cascade.detectMultiScale(frame, scaleFactor=1.1, minNeighbors=6, minSize=(min_face, min_face))
        if len(faces):
            x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
            out.append(((x + w / 2) / sw, (y + h / 2) / sh, w / sw))
        else:
            out.append(None)
    return out


def face_box(boxes: list[tuple[float, float, float] | None]) -> tuple[float, float, float] | None:
    """Posição típica (mediana) do rosto no trecho — usada para a webcam na tela dividida."""
    hits = [b for b in boxes if b]
    if not hits:
        return None
    arr = np.array(hits)
    return tuple(float(v) for v in np.median(arr, axis=0))


def camera_path(boxes: list[tuple[float, float, float] | None]) -> tuple[list[float], float]:
    """Preenche lacunas, filtra ruído e aplica zona morta para a câmera não ficar tremendo.

    Retorna (posições x por amostra, fração de amostras com rosto).
    """
    raw = [b[0] if b else None for b in boxes]
    if not raw:
        return [], 0.0
    hit = sum(v is not None for v in raw) / len(raw)
    if hit == 0:
        return [0.5] * len(raw), 0.0
    vals = list(raw)
    last = next(v for v in vals if v is not None)
    for i, v in enumerate(vals):  # mantém a última posição conhecida
        if v is None:
            vals[i] = last
        else:
            last = v
    arr = np.array(vals, dtype=float)
    k = 5  # mediana móvel remove detecções falsas isoladas
    padded = np.pad(arr, (k // 2, k // 2), mode="edge")
    med = np.array([np.median(padded[i : i + k]) for i in range(len(arr))])
    cam = med[0]
    path = []
    for target in med:
        if abs(target - cam) > 0.05:
            cam += (target - cam) * 0.3
        path.append(float(cam))
    return path, hit


def sendcmd_script(path: list[float], src_w: int, crop_w: int, dur: float, step: float = 0.1,
                   to_src=lambda t: t) -> tuple[str, int]:
    """Gera o script do filtro `sendcmd` que move o `crop` ao longo do clipe.

    `dur` é a duração do vídeo final; `to_src` converte esse tempo para o tempo relativo do
    trecho original (diferente da identidade quando há jump cuts)."""
    if not path:
        x = (src_w - crop_w) // 2
        return "", x
    times = np.arange(len(path)) / SAMPLE_FPS
    lines = []
    first_x = None
    t = 0.0
    while t <= dur + step:
        center = float(np.interp(to_src(t), times, path)) * src_w
        x = int(min(max(center - crop_w / 2, 0), src_w - crop_w))
        if first_x is None:
            first_x = x
        lines.append(f"{t:.2f} crop x {x};")
        t += step
    return "\n".join(lines) + "\n", first_x or 0
