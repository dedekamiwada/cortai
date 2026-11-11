"""Trilhas e efeitos sonoros ORIGINAIS, gerados por síntese (sem copyright).

Usados pela pós-edição quando a biblioteca do usuário não tem música daquele clima, e para
criar um kit básico de SFX na primeira vez. Tudo determinístico (mesma entrada → mesmo som).
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 48000
# climas que sabemos gerar; os outros só tocam se houver arquivo na biblioteca
GENERATED = ("tensao", "triste")


def write_wav(path: Path, audio: np.ndarray, peak: float = 0.8) -> Path:
    """Grava float (n,) ou (n, 2) como WAV 16 bits estéreo, normalizado para `peak`."""
    if audio.ndim == 1:
        audio = np.stack([audio, audio], axis=1)
    audio = audio / (np.max(np.abs(audio)) + 1e-9) * peak
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())
    return path


def _t(dur: float) -> np.ndarray:
    return np.arange(int(dur * SR)) / SR


def _lowpass_sweep(noise: np.ndarray, f0: float, f1: float) -> np.ndarray:
    """Passa-baixa de 1 polo com corte indo de f0 a f1 (curva quadrática)."""
    k = np.linspace(0, 1, len(noise))
    alpha = 1 - np.exp(-2 * np.pi * (f0 + (f1 - f0) * k ** 2) / SR)
    out = np.empty_like(noise)
    acc = 0.0
    for i in range(len(noise)):
        acc += alpha[i] * (noise[i] - acc)
        out[i] = acc
    return out


def _kick(dur: float = 0.35) -> np.ndarray:
    k = _t(dur)
    f = 55 + 70 * np.exp(-k * 30)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-k * 9)


def _place(out: np.ndarray, sig: np.ndarray, start: float, gain: float = 1.0, pan: float = 0.0) -> None:
    i = int(start * SR)
    j = min(len(out), i + len(sig))
    if j <= i:
        return
    left, right = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
    out[i:j, 0] += sig[: j - i] * gain * left
    out[i:j, 1] += sig[: j - i] * gain * right


# ------------------------------------------------------------------ músicas

def _tensao(dur: float) -> np.ndarray:
    """Drone grave + batida de coração acelerando + riser nos últimos ~2,8 s."""
    t = _t(dur)
    out = np.zeros((len(t), 2))
    drone = (np.sin(2 * np.pi * 36.7 * t) + 0.6 * np.sin(2 * np.pi * 73.6 * t)
             + 0.3 * np.sin(2 * np.pi * 110.3 * t + 0.3 * np.sin(2 * np.pi * 0.25 * t)))
    drone *= np.clip(t / 1.0, 0, 1) * (0.5 + 0.5 * t / max(dur, 0.1))
    _place(out, drone, 0, 0.16)
    beat = 0.4
    while beat < dur - 0.1:
        prog = beat / dur
        bpm = 70 + 80 * prog ** 1.5
        gain = 0.35 + 0.4 * prog
        _place(out, _kick(), beat, gain)
        _place(out, _kick(), beat + 0.35 * 60 / bpm, gain * 0.6)
        beat += 60 / bpm
    rise = min(2.8, dur * 0.6)
    k = _t(rise)
    noise = np.random.default_rng(7).standard_normal(len(k))
    tone = np.sin(2 * np.pi * np.cumsum(220 + 660 * (k / rise) ** 2) / SR)
    riser = (_lowpass_sweep(noise, 300, 7300) * 0.8 + tone * 0.25) * (k / rise) ** 1.8
    _place(out, riser, dur - rise, 0.35)
    return out


def _freq(midi: int) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _piano(midi: int, dur: float, vel: float = 1.0) -> np.ndarray:
    k = _t(dur + 1.8)
    f = _freq(midi)
    sig = np.zeros(len(k))
    for h, amp in enumerate((1, 0.45, 0.22, 0.12, 0.06, 0.03), start=1):
        sig += amp * np.sin(2 * np.pi * f * h * k * (1 + 0.0004 * h * h)) * np.exp(-k * (1.2 + 0.9 * h))
    return sig * np.clip(k / 0.004, 0, 1) * np.clip((dur + 1.8 - k) / 1.8, 0, 1) * vel


def _pad(midis: list[int], dur: float) -> np.ndarray:
    k = _t(dur)
    sig = np.zeros(len(k))
    for m in midis:
        for det in (-0.12, 0.0, 0.12):
            ph = 2 * np.pi * _freq(m) * 2 ** (det / 12) * k
            sig += np.sin(ph) + 0.3 * np.sin(2 * ph) + 0.12 * np.sin(3 * ph)
    env = np.minimum(np.clip(k / 1.2, 0, 1), np.clip((dur - k) / 1.4, 0, 1)) * 0.85
    return sig * (1 + 0.1 * np.sin(2 * np.pi * 5 * k)) * env / (len(midis) * 3)


# Ré menor: Dm - Bb - F - A, 4 tempos cada (baixo, acorde, melodia)
_ACORDES = (
    (50, [62, 65, 69], [74, 72, 69, 65]),
    (46, [62, 65, 70], [74, 72, 70, 65]),
    (41, [60, 65, 69], [72, 69, 65, 60]),
    (45, [61, 64, 69], [73, 69, 64, 61]),
)


def _triste(dur: float) -> np.ndarray:
    """Piano triste em Ré menor (66 bpm) com cordas a partir do 2º compasso."""
    out = np.zeros((int((dur + 2.5) * SR), 2))
    beat_s = 60 / 66
    bar = 4 * beat_s
    pos, i = 0.0, 0
    while pos < dur:
        bass, chord, melody = _ACORDES[i % 4]
        _place(out, _piano(bass - 12, bar, 0.9), pos, 0.30, -0.2)
        _place(out, _piano(bass, bar, 0.6), pos, 0.30, -0.2)
        for b in range(4):
            _place(out, _piano(melody[b], beat_s * 1.2, 0.8 if b == 0 else 0.6), pos + b * beat_s, 0.28, 0.25)
            _place(out, _piano(chord[b % 3] - 12, beat_s, 0.35), pos + (b + 0.5) * beat_s, 0.22)
        if i >= 1:
            _place(out, _pad(chord, bar + 0.4), pos, 0.20)
        pos += bar
        i += 1
    return out[: int(dur * SR)]


def music(clima: str, dur: float) -> np.ndarray | None:
    """Trilha original de `dur` segundos para o clima, ou None se não sabemos gerar esse clima."""
    if dur <= 0.2:
        return None
    if clima == "tensao":
        audio = _tensao(dur)
    elif clima == "triste":
        audio = _triste(dur)
    else:
        return None
    n = len(audio)
    fade = np.ones(n)
    fi, fo = min(n, int(0.3 * SR)), min(n, int(0.6 * SR))
    fade[:fi] = np.linspace(0, 1, fi)
    fade[n - fo:] *= np.linspace(1, 0, fo)
    return audio * fade[:, None]


# ------------------------------------------------------------------ efeitos sonoros

def _whoosh() -> np.ndarray:
    k = _t(0.45)
    noise = np.random.default_rng(1).standard_normal(len(k))
    env = np.sin(np.pi * np.clip(k / 0.45, 0, 1)) ** 2
    return _lowpass_sweep(noise, 400, 5000) * env


def _boom() -> np.ndarray:
    k = _t(1.3)
    f = 38 + 90 * np.exp(-k * 12)
    body = np.tanh(2.5 * np.sin(2 * np.pi * np.cumsum(f) / SR)) * np.exp(-k * 2.6)
    click = np.random.default_rng(2).standard_normal(len(k)) * np.exp(-k * 60) * 0.4
    return body + click


def _impacto() -> np.ndarray:
    k = _t(0.6)
    noise = np.random.default_rng(3).standard_normal(len(k))
    return (np.sin(2 * np.pi * np.cumsum(70 + 160 * np.exp(-k * 25)) / SR) * np.exp(-k * 7)
            + _lowpass_sweep(noise, 6000, 800) * np.exp(-k * 14) * 0.6)


def _ding() -> np.ndarray:
    k = _t(1.4)
    return sum(a * np.sin(2 * np.pi * 1318.5 * r * k) * np.exp(-k * d)
               for a, r, d in ((1, 1, 3), (0.4, 2.76, 5), (0.2, 5.4, 8)))


def _riser() -> np.ndarray:
    k = _t(2.0)
    noise = np.random.default_rng(4).standard_normal(len(k))
    tone = np.sin(2 * np.pi * np.cumsum(200 + 900 * (k / 2.0) ** 2) / SR)
    return (_lowpass_sweep(noise, 300, 8000) * 0.8 + tone * 0.3) * (k / 2.0) ** 1.6


def _queda() -> np.ndarray:
    """Tom descendo (fracasso), tipo 'perdeu'."""
    k = _t(1.1)
    f = 520 * np.exp(-k * 1.4)
    tone = np.sign(np.sin(2 * np.pi * np.cumsum(f) / SR)) * 0.5 + np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.5
    return tone * np.clip((1.1 - k) / 0.3, 0, 1) * 0.6


DEFAULT_SFX = {
    "whoosh": _whoosh,
    "boom": _boom,
    "impacto": _impacto,
    "ding": _ding,
    "riser": _riser,
    "queda": _queda,
}


def ensure_default_sfx(folder: Path) -> None:
    """Cria o kit básico (cortai-*.wav) se ainda não existir. Nunca sobrescreve nada do usuário."""
    for name, make in DEFAULT_SFX.items():
        path = folder / f"cortai-{name}.wav"
        if not path.exists():
            write_wav(path, make(), peak=0.9)
