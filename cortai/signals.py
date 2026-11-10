"""Sinais de áudio que a transcrição não mostra: gritos, risadas, barulho do jogo, silêncios.

Em live de jogo o momento bom raramente é uma frase: é uma explosão sem fala, uma risada
que o Whisper ignora ou um silêncio de choque. Esses eventos viram marcadores na
transcrição enviada ao Claude.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

HOP = 0.25  # segundos por amostra de volume


def loudness(wav_path: Path) -> list[float]:
    """Volume (dBFS) a cada 0,25 s, lendo o WAV em blocos para não carregar 2h na memória."""
    out: list[float] = []
    with wave.open(str(wav_path), "rb") as wav:
        rate = wav.getframerate()
        hop = int(rate * HOP)
        while True:
            raw = wav.readframes(hop * 240)  # 1 minuto por bloco
            if not raw:
                break
            x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
            n = len(x) // hop
            if n == 0:
                break
            frames = x[: n * hop].reshape(n, hop)
            rms = np.sqrt(np.mean(frames ** 2, axis=1))
            out.extend(np.round(20 * np.log10(rms + 1e-6), 1).tolist())
    return out


def _speech_mask(n: int, words: list[dict]) -> np.ndarray:
    mask = np.zeros(n, dtype=bool)
    for w in words:
        a, b = int(w["s"] / HOP), int(np.ceil(w["e"] / HOP))
        mask[max(0, a) : min(n, b)] = True
    return mask


def talk_level(loud: list[float], words: list[dict]) -> float:
    """Volume típico de fala do vídeo (mediana dos trechos com palavras)."""
    if not loud:
        return -30.0
    db = np.array(loud)
    speech = _speech_mask(len(db), words)
    return float(np.median(db[speech])) if speech.any() else float(np.median(db))


def audio_events(loud: list[float], words: list[dict]) -> list[dict]:
    """Picos de volume e trechos sem fala, relativos ao nível normal de fala do próprio vídeo."""
    if not loud:
        return []
    db = np.array(loud)
    speech = _speech_mask(len(db), words)
    base = talk_level(loud, words)
    events: list[dict] = []

    # 1) picos: bem acima do nível normal de fala
    loud_mask = db > base + 7
    i = 0
    while i < len(db):
        if not loud_mask[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(db) and (loud_mask[j + 1] or (j + 2 < len(db) and loud_mask[j + 2])):
            j += 1
        seg = slice(i, j + 1)
        with_speech = speech[seg].mean() > 0.5
        events.append({
            "t": round(i * HOP, 2), "dur": round((j - i + 1) * HOP, 2),
            "kind": "grito" if with_speech else "som_alto",
            "db": round(float(db[seg].max() - base), 1),
        })
        i = j + 1

    # 2) intervalos entre palavras: silêncio de choque ou barulho sem fala (risada, jogo)
    starts = [w["s"] for w in words]
    ends = [w["e"] for w in words]
    for k in range(1, len(words)):
        gap = starts[k] - ends[k - 1]
        if gap < 2.5:
            continue
        a, b = int(ends[k - 1] / HOP), int(starts[k] / HOP)
        # percentil 85: uma risada curta dentro de um intervalo longo ainda conta como "com som"
        level = float(np.percentile(db[a:b], 85)) if b > a else -90.0
        kind = "sem_fala_com_som" if level > base - 12 else "silencio"
        events.append({"t": round(ends[k - 1], 2), "dur": round(gap, 2), "kind": kind,
                       "db": round(level - base, 1)})
    events.sort(key=lambda e: e["t"])
    return events


LABELS = {
    "grito": "🔊 fala muito alta/grito",
    "som_alto": "💥 som alto SEM fala (risada, grito ou som do jogo)",
    "sem_fala_com_som": "🎮 sem fala mas com som (risada, reação ou jogo rolando)",
    "silencio": "🤐 silêncio",
}


def describe(event: dict) -> str:
    extra = f", +{event['db']:.0f} dB" if event["kind"] in ("grito", "som_alto") else ""
    return f"[{event['t']:.2f}] <{LABELS[event['kind']]} por {event['dur']:.1f}s{extra}>"


def window(loud: list[float], start: float, end: float) -> list[float]:
    return loud[max(0, int(start / HOP)) : int(end / HOP) + 1]
