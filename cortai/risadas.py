"""Risadas na live: o momento bom da zoeira com os amigos quase nunca é uma frase.

Nos 13 cortes de "zoeira com amigos" analisados, 6 têm o pico em gargalhada sem nenhuma palavra (um deles
passa 14 s só rindo). O Whisper vê isso como silêncio, ou inventa uma frase por cima. Aqui roda o detector
de risada da Zencastr (MIT, https://huggingface.co/zencastr/laughter-detection): janelas de 4 s a cada 2 s,
probabilidade de risada a cada 0,16 s. Nos 13 cortes ele achou as risadas que se veem no vídeo e quase nada
nos que só têm grito ou instrução (z6, z10).

O modelo (~360 MB) baixa do Hugging Face na primeira vez, como o do Whisper. Roda no onnxruntime (que já vem
com o faster-whisper); na CPU leva uns minutos numa live de 2 h, por isso só roda quando o estilo pede.
"""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Callable

import numpy as np

REPO = "zencastr/laughter-detection"
REVISION = "849623f8338341d7e07be00a487337e0086ec14d"  # fixa: o executável deve sair igual a cada build
SR = 16000
WIN, HOP_S, FRAME = 4.0, 2.0, 0.16
FRAMES_PER_WIN = 24
BATCH = 8
# decodificação: o ponto "high_precision" do modelo (limiar 0,99) perdia risada com o som do jogo por baixo;
# 0,9 bateu com o que se vê nos 13 cortes de referência sem pegar grito nem instrução gritada
THRESHOLD, MIN_DUR, MERGE_GAP = 0.9, 0.3, 1.0
STRONG = 1.5      # s: risada de verdade (não um "hah" solto) — vira marcador e serve para o fim e o rosto
QUIET_DB = -45.0  # janela inteira abaixo disso nem roda o modelo (silêncio)

_session = None


def _load(on_status: Callable[[str], None] | None = None):
    global _session
    if _session is not None:
        return _session
    import onnxruntime as ort
    from huggingface_hub import hf_hub_download, try_to_load_from_cache

    cached = try_to_load_from_cache(REPO, "model.onnx", revision=REVISION)
    if not isinstance(cached, str) and on_status:
        on_status("Baixando o detector de risada (360 MB, só na primeira vez)…")
    path = hf_hub_download(REPO, "model.onnx", revision=REVISION)
    providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]
    _session = ort.InferenceSession(path, providers=providers)
    return _session


def _blocks(wav_path: Path, seconds: float = 120.0):
    """(início em s, amostras float32) em blocos que se sobrepõem 2 s, para não carregar 2 h na memória."""
    with wave.open(str(wav_path), "rb") as w:
        if w.getframerate() != SR or w.getnchannels() != 1:
            raise ValueError("o detector de risada espera WAV mono 16 kHz")
        step, over = int(seconds * SR), int((WIN - HOP_S) * SR)
        pos, tail = 0, np.zeros(0, np.float32)
        while True:
            raw = w.readframes(step)
            x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
            if not len(x):
                if len(tail):
                    yield (pos - len(tail)) / SR, tail
                return
            buf = np.concatenate([tail, x])
            yield (pos - len(tail)) / SR, buf
            pos += len(x)
            tail = buf[-over:] if len(buf) > over else buf
            if len(x) < step:
                return


def frame_probs(wav_path: Path, duration: float, on_progress: Callable[[float], None] | None = None,
                on_status: Callable[[str], None] | None = None) -> np.ndarray:
    """Probabilidade de risada a cada 0,16 s do áudio inteiro (máximo entre janelas que se sobrepõem)."""
    sess = _load(on_status)
    W, H = int(WIN * SR), int(HOP_S * SR)
    grid = np.zeros(int(duration / FRAME) + FRAMES_PER_WIN + 2, np.float32)
    done_until = 0.0
    for t0, x in _blocks(wav_path):
        starts = [s for s in range(0, max(len(x) - W, 0) + 1, H) if t0 + s / SR >= done_until - 1e-6]
        if not starts:
            continue
        rows, valid, ts = [], [], []
        for s in starts:
            seg = x[s:s + W]
            rms = float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0
            if 20 * np.log10(rms + 1e-6) < QUIET_DB:
                continue
            rows.append(np.pad(seg, (0, W - len(seg))))
            valid.append(len(seg))
            ts.append(t0 + s / SR)
        for b in range(0, len(rows), BATCH):
            _, fp, fm = sess.run(None, {"waveform": np.stack(rows[b:b + BATCH]).astype(np.float32),
                                        "n_valid": np.array(valid[b:b + BATCH], np.int64)})
            for t, p, m in zip(ts[b:b + BATCH], fp, fm):
                g0 = int(t / FRAME)
                for k in range(FRAMES_PER_WIN):
                    if m[k] and g0 + k < len(grid):
                        grid[g0 + k] = max(grid[g0 + k], p[k])
        done_until = t0 + (starts[-1] + H) / SR
        if on_progress and duration:
            on_progress(min(1.0, done_until / duration))
    return grid[: int(duration / FRAME) + 1]


def events(grid: np.ndarray, loud: list[float] | None = None) -> list[dict]:
    """Risadas: [{t, dur, score, pico}] — `pico` é o segundo mais alto da risada (onde mirar o rosto)."""
    hot = grid >= THRESHOLD
    spans, start = [], None
    for i, h in enumerate(hot):
        if h and start is None:
            start = i
        elif not h and start is not None:
            spans.append([start, i])
            start = None
    if start is not None:
        spans.append([start, len(grid)])
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and (a - merged[-1][1]) * FRAME <= MERGE_GAP:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    out = []
    hop = 0.25
    for a, b in merged:
        t, e = a * FRAME, b * FRAME
        if e - t < MIN_DUR:
            continue
        pico = t + float(np.argmax(grid[a:b])) * FRAME
        if loud:
            seg = loud[int(t / hop): int(e / hop) + 1]
            if seg:
                pico = int(t / hop) * hop + float(np.argmax(seg)) * hop + hop / 2
        out.append({"t": round(t, 2), "dur": round(e - t, 2), "score": round(float(grid[a:b].max()), 3),
                    "pico": round(min(max(pico, t), e), 2)})
    return out


def strong(laughs: list[dict]) -> list[dict]:
    return [r for r in laughs if r["dur"] >= STRONG]


def inside(laughs: list[dict], start: float, end: float) -> list[dict]:
    return [r for r in laughs if r["t"] < end and r["t"] + r["dur"] > start]


# ---- fim na risada: o corte termina no auge, não na última frase
END_MAX_EXTRA = 4.0


def end_on_laugh(start: float, end: float, laughs: list[dict], duration: float, max_len: float) -> float | None:
    """Novo fim se uma risada forte está rolando no fim do corte (ou começa logo depois): vai até ela acabar,
    no máximo 2,5 s depois do auge e 4 s além do fim original.

    Só estica: cortar a fala depois da risada pode arrancar a piada, e isso o Claude decide pelo guia.
    """
    near = [r for r in strong(laughs) if r["t"] <= end + 1.5 and r["t"] + r["dur"] > end + 0.3]
    if not near:
        return None
    r = max(near, key=lambda r: r["t"])
    want = min(r["t"] + r["dur"] + 0.3, max(r["pico"], end) + 2.5)
    new = min(want, end + END_MAX_EXTRA, start + max_len + END_MAX_EXTRA, duration)
    return round(new, 2) if new > end + 0.3 else None


# ---- rosto em tela cheia no pico da risada
FACE_HOLD = 1.6   # s
FACE_GAP = 6.0    # s mínimos entre dois
FACE_MAX = 3      # por corte


def face_shots(clip_start: float, clip_end: float, laughs: list[dict]) -> list[float]:
    """Segundos (na live) em que a câmera do streamer ocupa a tela toda: os picos das risadas mais longas."""
    cand = sorted(strong(inside(laughs, clip_start, clip_end)), key=lambda r: -r["dur"])
    out: list[float] = []
    for r in cand:
        t = r["pico"] - 0.3
        if clip_start + 0.8 <= t <= clip_end - 0.6 and all(abs(t - o) >= FACE_GAP for o in out):
            out.append(round(t, 2))
        if len(out) >= FACE_MAX:
            break
    return sorted(out)
