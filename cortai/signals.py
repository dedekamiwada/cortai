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


# Susto (jumpscare): o volume SALTA de um trecho baixo para bem acima da fala. Calibrado em 11 cortes de
# terror com 300 mil+ views: pega os sustos que aparecem no áudio; quem fala sem parar ou já tem música por
# baixo não tem salto — aí quem acha o susto é a revisão visual.
SCARE_JUMP = 15       # dB: máximo nos 2 s seguintes menos a mediana dos 4 s antes
SCARE_QUIET_DB = -4   # os 4 s antes ficam abaixo da fala normal
SCARE_LOUD_DB = 8     # e o salto passa bem da fala normal (só voltar a falar depois de uma pausa não conta)


def scare_times(db: np.ndarray, base: float) -> list[tuple[float, float, float]]:
    """(segundo, salto em dB, nível dos 4 s antes) dos sustos prováveis, no máximo um a cada 3 s."""
    out: list[tuple[float, float, float]] = []
    before_n, after_n = int(4 / HOP), int(2 / HOP)
    i = before_n
    while i < len(db) - 1:
        before = float(np.median(db[i - before_n:i - 1]))
        after = db[i:i + after_n]
        top = float(after.max())
        if before <= base + SCARE_QUIET_DB and top >= base + SCARE_LOUD_DB and top - before >= SCARE_JUMP:
            k = i + int(np.argmax(after > before + 6))  # onde o som começa a subir
            out.append((round(k * HOP, 2), round(top - before, 1), round(before - base, 1)))
            i = k + int(3 / HOP)
            continue
        i += 1
    return out


def _mark_scares(events: list[dict], db: np.ndarray, base: float) -> None:
    """Marca como "susto" o pico que vem de um silêncio (ou cria o evento, se o pico não foi listado)."""
    for t, jump, before in scare_times(db, base):
        near = [e for e in events if e["kind"] in ("grito", "som_alto") and t - 0.75 <= e["t"] <= t + 2]
        if near:
            near[0].update(kind="susto", jump=jump, t=min(near[0]["t"], t))
        else:
            events.append({"t": t, "dur": 1.0, "kind": "susto", "db": round(jump + before, 1), "jump": jump})


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
    _mark_scares(events, db, base)

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



# Pico de reação normalizado pelo próprio streamer: z-score robusto (mediana/MAD) numa janela de ~5 min
# centrada no trecho, para compensar ganho do microfone e cansaço ao longo da live (recomendação da pesquisa
# "Cortes de jogos e IAs de clipes"; os limiares são ponto de partida a calibrar).
Z_WINDOW = 300.0     # s de contexto para a linha de base
Z_BLOCK = 30.0       # s: a linha de base é recalculada a cada bloco (barato numa live de 3 h)
Z_MIN = 4.5          # z robusto mínimo: ~60 picos/h numa live de 2,25 h (com 3.0 eram 175/h)
Z_GAP = 8.0          # s mínimos entre picos (fica o mais forte)


def robust_peaks(loud: list[float], z_min: float = Z_MIN) -> list[dict]:
    """Picos de volume em z robusto contra a vizinhança de 5 min: [{t, z, db}] em ordem de tempo."""
    if not loud:
        return []
    db = np.asarray(loud, dtype=np.float32)
    n, blk, half = len(db), int(Z_BLOCK / HOP), int(Z_WINDOW / HOP / 2)
    z = np.zeros(n, dtype=np.float32)
    for a in range(0, n, blk):
        b = min(n, a + blk)
        c = (a + b) // 2
        ctx = db[max(0, c - half): min(n, c + half)]
        med = float(np.median(ctx))
        mad = float(np.median(np.abs(ctx - med))) * 1.4826 or 1.0
        z[a:b] = (db[a:b] - med) / max(mad, 1.0)
    peaks: list[dict] = []
    for i in np.argsort(-z):
        if z[i] < z_min:
            break
        t = i * HOP
        if all(abs(t - p["t"]) >= Z_GAP for p in peaks):
            peaks.append({"t": round(t, 2), "z": round(float(z[i]), 1), "db": round(float(db[i]), 1)})
    return sorted(peaks, key=lambda p: p["t"])

LABELS = {
    "susto": "😱 SUSTO PROVÁVEL: silêncio e de repente som alto",
    "grito": "🔊 fala muito alta/grito",
    "som_alto": "💥 som alto SEM fala (risada, grito ou som do jogo)",
    "sem_fala_com_som": "🎮 sem fala mas com som (risada, reação ou jogo rolando)",
    "silencio": "🤐 silêncio",
    # eventos do jogo lidos no placar (hud.py) e reação normalizada (robust_peaks)
    "gol_nosso": "⚽ GOL DO TIME DO STREAMER (lido no placar)",
    "gol_deles": "🥅 GOL SOFRIDO (lido no placar)",
    "inicio": "🏁 começo de partida",
    "fim_do_tempo": "⏱ fim do tempo normal → prorrogação",
    "reacao": "📈 reação forte (volume muito acima do normal dele neste trecho da live)",
}


def describe(event: dict) -> str:
    kind = event["kind"]
    if kind in ("gol_nosso", "gol_deles"):
        return (f"[{event['t']:.2f}] <{LABELS[kind]}: placar {event.get('placar') or '?'}"
                f"{', relógio ' + event['relogio'] if event.get('relogio') else ''}; replay até {event.get('fim_pausa', event['t']):.0f}s>")
    if kind in ("inicio", "fim_do_tempo"):
        return f"[{event['t']:.2f}] <{LABELS[kind]}>"
    if kind == "reacao":
        return f"[{event['t']:.2f}] <{LABELS[kind]}: z={event.get('z', 0):.1f}>"
    extra = f", +{event['db']:.0f} dB" if event["kind"] in LOUD_KINDS else ""
    if event["kind"] == "susto":
        extra += f", salto de {event.get('jump', 0):.0f} dB vindo de um trecho baixo"
    return f"[{event['t']:.2f}] <{LABELS[event['kind']]} por {event['dur']:.1f}s{extra}>"


LOUD_KINDS = ("grito", "som_alto", "susto")


def window(loud: list[float], start: float, end: float) -> list[float]:
    return loud[max(0, int(start / HOP)) : int(end / HOP) + 1]
