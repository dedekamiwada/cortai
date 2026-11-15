"""Cortes feitos à mão antes do Cortaí: acha de que trecho da live cada um saiu e guarda como exemplo do gosto
do streamer (rótulo positivo de graça, a ideia de Fu et al. no relatório de pesquisa).

Tudo por código, sem IA: o volume do corte (dB a cada 0,25 s) é comparado com o volume da live por correlação
normalizada; o pico diz onde o corte começa. A correlação de Pearson ignora ganho e normalização, então um corte
com volume ajustado ainda casa; trilha alta por cima ou muitos jump cuts derrubam a confiança e o corte fica de fora.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import numpy as np

from . import media, signals

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
MAX_FILES = 40
MIN_R = 0.70          # correlação mínima do corte inteiro (testes: casos certos 0,74–0,87; errados ≤ 0,57)
MIN_MARGIN = 0.12     # folga sobre o 2º melhor lugar da live (errados: ≤ 0,03)
WIN, WIN_HOP = 8.0, 2.5  # corte com jump cuts: janelas de 8 s alinhadas uma a uma
WIN_R = 0.80
EXAMPLES_MAX = 30     # exemplos guardados; o prompt usa só os mais recentes


def envelope(video: Path) -> list[float]:
    with tempfile.TemporaryDirectory() as tmp:
        wav = media.extract_audio(video, Path(tmp) / "a.wav")
        return signals.loudness(wav)


def _feature(env) -> np.ndarray:
    """Nível relativo à mediana dos 2 s em volta: imune a normalização (loudnorm/compressor) e a música de fundo
    constante. Medido contra o nível puro e a derivada: só esta casou os cortes com volume normalizado."""
    x = np.asarray(env, float)
    x = np.maximum(np.where(np.isfinite(x), x, -90.0), -60.0)
    k = 8
    win = np.lib.stride_tricks.sliding_window_view(np.pad(x, k, mode="edge"), 2 * k + 1)
    return x - np.median(win, axis=1)


def _match(clip: np.ndarray, live: np.ndarray) -> tuple[int, float, float] | None:
    """(posição, r, folga) do melhor encaixe de `clip` dentro de `live` por correlação normalizada."""
    m = len(clip)
    if m < 8 or len(live) < m or clip.std() < 1e-3:
        return None
    x = (clip - clip.mean()) / clip.std()
    c1 = np.concatenate([[0.0], np.cumsum(live)])
    c2 = np.concatenate([[0.0], np.cumsum(live * live)])
    mu = (c1[m:] - c1[:-m]) / m
    sd = np.sqrt(np.maximum((c2[m:] - c2[:-m]) / m - mu * mu, 1e-9))
    r = np.correlate(live, x, mode="valid") / (m * sd)
    i = int(np.argmax(r))
    away = np.concatenate([r[: max(0, i - m)], r[i + m:]])  # 2º melhor fora da vizinhança do 1º
    margin = float(r[i] - away.max()) if away.size else 1.0
    return i, float(r[i]), margin


def align(clip_env: list[float], live_env: list[float]) -> dict | None:
    """Onde o corte está na live (segundos) e com que confiança. `ok` só quando casa com segurança."""
    clip, live = _feature(clip_env), _feature(live_env)
    hop = signals.HOP
    whole = _match(clip, live)
    if whole and whole[1] >= MIN_R and whole[2] >= MIN_MARGIN:
        i, r, margin = whole
        return {"start": round(i * hop, 2), "end": round((i + len(clip)) * hop, 2), "r": round(r, 3),
                "folga": round(margin, 3), "por": "inteiro", "ok": True}
    # jump cuts: janelas de 8 s votam no segundo da live em que o corte "começaria"; janelas verdadeiras
    # concordam entre si (±1 s) e as falsas se espalham. Vale com 3+ votos em grupos na ordem do corte.
    w, step = int(WIN / hop), int(WIN_HOP / hop)
    votes = []
    for a in range(0, max(1, len(clip) - w + 1), step):
        got = _match(clip[a:a + w], live)
        if got and got[1] >= WIN_R:
            votes.append((got[0] - a, a, got[1]))
    groups: list[list] = []
    for v in sorted(votes):
        if groups and v[0] - groups[-1][-1][0] <= 4:
            groups[-1].append(v)
        else:
            groups.append([v])
    real = sorted((g for g in groups if len(g) >= 2), key=lambda g: min(x[1] for x in g))
    real = [g for k, g in enumerate(real) if k == 0 or g[0][0] >= real[k - 1][0][0]]  # pedaços em ordem na live
    if sum(len(g) for g in real) >= 3 and real[-1][0][0] - real[0][0][0] <= int(120 / hop):
        start, end = real[0][0][0], real[-1][0][0] + len(clip)
        return {"start": round(max(0, start) * hop, 2), "end": round(end * hop, 2),
                "r": round(float(np.mean([x[2] for g in real for x in g])), 3), "folga": None,
                "por": f"{sum(len(g) for g in real)} janelas em {len(real)} pedaço(s)", "ok": True}
    i, r, margin = whole if whole else (0, 0.0, 0.0)
    return {"start": round(i * hop, 2), "end": round((i + len(clip)) * hop, 2), "r": round(r, 3),
            "folga": round(margin, 3), "por": "inteiro", "ok": False}


def examples_path(root: Path) -> Path:
    return root / "_exemplos" / "exemplos.json"


def load_examples(root: Path) -> list[dict]:
    try:
        return json.loads(examples_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_examples(root: Path, job_id: str, found: list[dict], words: list[dict]) -> int:
    """Guarda os cortes casados como exemplos (com a fala do trecho). Devolve quantos entraram."""
    items = [e for e in load_examples(root) if not (e.get("job") == job_id and e.get("arquivo") in {f["arquivo"] for f in found})]
    added = 0
    for f in found:
        if not f.get("ok"):
            continue
        text = " ".join(w["w"].strip() for w in words if f["start"] <= w["s"] < f["end"])
        items.append({"job": job_id, "arquivo": f["arquivo"], "start": f["start"], "end": f["end"],
                      "texto": text[:400], "quando": round(time.time())})
        added += 1
    items = sorted(items, key=lambda e: e["quando"])[-EXAMPLES_MAX:]
    examples_path(root).parent.mkdir(parents=True, exist_ok=True)
    examples_path(root).write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return added


def guide(root: Path, limit: int = 4) -> str:
    """Para a escolha de cortes: alguns cortes que o streamer fez à mão (o gosto dele). ~150 tokens."""
    ex = [e for e in load_examples(root) if e.get("texto")][-limit:]
    if not ex:
        return ""
    lines = [f"- {e['end'] - e['start']:.0f} s: \"{e['texto'][:160]}\"" for e in ex]
    return ("\n\nCortes que o próprio streamer fez à mão (é o gosto dele; procure momentos do mesmo tipo, "
            "não as mesmas frases):\n" + "\n".join(lines))
