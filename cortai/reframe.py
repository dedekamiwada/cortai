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



# ------------------------------------------------------------------ movimento do rosto (reação)
MOTION_FPS = 6
MOTION_PAD = 6.0  # s de folga para a linha de base: numa janela de 4 s a própria reação domina a mediana


def motion_peak(src: Path, start: float, end: float, src_w: int, src_h: int,
                face: tuple[float, float, float] | None) -> tuple[float, float] | None:
    """Segundo de maior movimento na região do rosto e seu z robusto contra o próprio trecho.

    Medido numa live real: o pico de movimento caiu em 3184,0 s onde o Claude viu "saindo da cadeira" em
    3183,9 s (z=7,6). Usado para mirar o zoom de reação no instante certo. None sem rosto ou trecho curto."""
    if face is None or end - start < 1.0:
        return None
    cx, cy, fw = face
    side = min(1.0, fw * 3.2)  # a reação usa ombros e mãos, não só o rosto
    x0 = int(max(0.0, cx - side / 2) * src_w)
    y0 = int(max(0.0, cy - side * 0.6) * src_h)
    cw = int(min(src_w - x0, side * src_w)) // 2 * 2
    ch = int(min(src_h - y0, side * src_w * 1.1)) // 2 * 2
    if cw < 16 or ch < 16:
        return None
    w, h = 160, max(16, int(160 * ch / cw) // 2 * 2)
    a0 = max(0.0, start - MOTION_PAD)
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{a0:.3f}", "-t", f"{end + MOTION_PAD - a0:.3f}", "-i", str(src),
           "-vf", f"crop={cw}:{ch}:{x0}:{y0},scale={w}:{h},fps={MOTION_FPS},format=gray",
           "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    n = len(raw) // (w * h)
    if n < 3:
        return None
    fr = np.frombuffer(raw[: n * w * h], np.uint8).reshape(n, h, w).astype(np.float32)
    energy = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))
    med = float(np.median(energy))
    mad = float(np.median(np.abs(energy - med))) * 1.4826 + 1e-6
    times = a0 + (np.arange(len(energy)) + 1) / MOTION_FPS
    inside = np.where((times >= start) & (times <= end))[0]
    if not len(inside):
        return None
    k = int(inside[np.argmax(energy[inside])])
    return float(times[k]), float((energy[k] - med) / mad)


# ---- borda da webcam sobreposta (para o rosto em tela cheia não pegar pedaço do jogo)
CAM_EDGE_MIN = 10.0   # degrau de cinza (0-255) que a borda mantém em quase todos os quadros


def cam_rect(src: Path, start: float, dur: float, src_w: int, src_h: int,
             face: tuple[float, float, float] | None, samples: int = 16) -> tuple[float, float, float, float] | None:
    """Retângulo da webcam em volta do rosto (x0, y0, x1, y1 normalizados), achado pelas bordas retas que ficam
    paradas enquanto o jogo se mexe: mediana no tempo do degrau de cinza, linha inteira na altura/largura do
    rosto. Lado sem borda achada = a webcam vai até a beirada do quadro. None sem rosto."""
    if face is None or dur <= 0:
        return None
    sw = 480
    sh = max(2, int(round(src_h * sw / src_w / 2)) * 2)
    fps = max(0.2, min(4.0, samples / dur))
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
           "-vf", f"fps={fps:.3f},scale={sw}:{sh},format=gray", "-frames:v", str(samples), "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    n = len(raw) // (sw * sh)
    if n < 3:
        return None
    fr = np.frombuffer(raw[: n * sw * sh], np.uint8).reshape(n, sh, sw).astype(np.int16)
    # degrau entre faixas de 3 px de cada lado (borda borrada pela compressão também conta), mediana no tempo
    k = 3
    cs_y = np.cumsum(np.pad(fr, ((0, 0), (1, 0), (0, 0))), axis=1, dtype=np.int32)
    band_y = (cs_y[:, k:, :] - cs_y[:, :-k, :]) / k              # média das linhas [i, i+k)
    gy = np.zeros((sh - 1, sw), np.float32)
    gy[k - 1:sh - k] = np.median(np.abs(band_y[:, k:] - band_y[:, :-k]), axis=0)  # borda entre i e i+1
    cs_x = np.cumsum(np.pad(fr, ((0, 0), (0, 0), (1, 0))), axis=2, dtype=np.int32)
    band_x = (cs_x[:, :, k:] - cs_x[:, :, :-k]) / k
    gx = np.zeros((sh, sw - 1), np.float32)
    gx[:, k - 1:sw - k] = np.median(np.abs(band_x[:, :, k:] - band_x[:, :, :-k]), axis=0)
    cx, cy, fw = face[0] * sw, face[1] * sh, face[2] * sw
    xa, xb = int(max(0, cx - fw)), int(min(sw - 1, cx + fw))
    ya, yb = int(max(0, cy - fw * 0.8)), int(min(sh - 1, cy + fw * 0.8))

    sd = fr.std(axis=0)  # o jogo se mexe; o fundo da webcam fica parado

    def contrast(i: int, horizontal: bool) -> bool:
        """A borda separa um lado que se mexe (jogo) de um que quase não se mexe (fundo da câmera)."""
        if horizontal:
            a, b = sd[max(0, i - 4):i + 1, xa:xb + 1], sd[i + 1:i + 6, xa:xb + 1]
        else:
            a, b = sd[ya:yb + 1, max(0, i - 4):i + 1], sd[ya:yb + 1, i + 1:i + 6]
        if not a.size or not b.size:
            return False
        ma, mb = float(a.mean()), float(b.mean())
        return max(ma, mb) > 1.8 * min(ma, mb) + 2.0

    def find(profile_fn, lo: int, hi: int, toward_lo: bool, horizontal: bool) -> int | None:
        rng = range(hi, lo - 1, -1) if toward_lo else range(lo, hi + 1)
        strong = [i for i in rng if profile_fn(i) >= CAM_EDGE_MIN]
        # a 1ª linha forte saindo do rosto pode ser uma prateleira do quarto: fica a que também separa movimento
        return next((i for i in strong if contrast(i, horizontal)), strong[0] if strong else None)

    row = lambda y: float(np.median(gy[y, xa:xb + 1])) if 0 <= y < gy.shape[0] else 0.0
    col = lambda x: float(np.median(gx[ya:yb + 1, x])) if 0 <= x < gx.shape[1] else 0.0
    mx, my = int(fw * 0.7), int(fw * 0.3)
    top = find(row, int(max(0, cy - fw * 4)), int(cy - fw * 0.6), True, True)
    bot = find(row, int(cy + my), int(min(sh - 2, cy + fw * 4)), False, True)
    left = find(col, int(max(0, cx - fw * 5)), int(cx - mx), True, False)
    right = find(col, int(cx + mx), int(min(sw - 2, cx + fw * 5)), False, False)
    x0 = (left + 1) / sw if left is not None else 0.0
    x1 = (right + 1) / sw if right is not None else 1.0
    y0 = (top + 1) / sh if top is not None else 0.0
    y1 = (bot + 1) / sh if bot is not None else 1.0
    return (x0, y0, x1, y1)
