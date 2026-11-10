"""Cortes dentro do clipe (jump cuts): remove pausas mortas e mapeia tempos do original
para o vídeo final, para legendas, zoom e câmera continuarem sincronizados.

Pausa "morta" = intervalo entre palavras sem som relevante. Silêncio com risada, grito ou
som do jogo NÃO é removido — em corte de jogo esse silêncio muitas vezes é a piada.
"""
from __future__ import annotations

import numpy as np

from . import signals

MIN_GAP = 0.8      # só considera intervalos entre palavras maiores que isso
PAD = 0.18         # respiro mantido antes/depois de cada palavra
MIN_CUT = 0.35     # não vale cortar menos que isso
DEAD_BELOW = 6.0   # dB abaixo do nível de fala = só som ambiente/jogo de fundo (risada e grito ficam acima)


def keep_intervals(words: list[dict], loud: list[float], talk_level: float, start: float, end: float,
                   protect: list[tuple[float, float]] = ()) -> list[tuple[float, float]]:
    """Trechos a manter, em segundos relativos ao início do clipe."""
    inside = [w for w in words if w["s"] >= start and w["e"] <= end]
    db = np.array(loud) if loud else np.array([])
    cuts: list[tuple[float, float]] = []
    for a, b in zip(inside, inside[1:]):
        if b["s"] - a["e"] < MIN_GAP:
            continue
        g0, g1 = a["e"] + PAD, b["s"] - PAD
        if g1 - g0 < MIN_CUT:
            continue
        if any(g0 < p1 and g1 > p0 for p0, p1 in protect):
            continue
        i0, i1 = int(g0 / signals.HOP), int(g1 / signals.HOP) + 1
        if db.size and i1 > i0:
            level = float(np.percentile(db[i0:min(i1, db.size)], 85)) if i0 < db.size else -90.0
            if level > talk_level - DEAD_BELOW:
                continue  # tem som (risada, jogo): mantém
        cuts.append((g0 - start, g1 - start))
    keep, cur = [], 0.0
    for c0, c1 in cuts:
        keep.append((cur, c0))
        cur = c1
    keep.append((cur, end - start))
    return [(round(a, 3), round(b, 3)) for a, b in keep if b - a > 0.05]


class TimeMap:
    """Converte tempo relativo do clipe original <-> tempo do vídeo final com jump cuts."""

    def __init__(self, keep: list[tuple[float, float]]):
        self.keep = keep
        self.offsets = []
        acc = 0.0
        for a, b in keep:
            self.offsets.append(acc)
            acc += b - a
        self.duration = acc

    def to_out(self, t: float) -> float:
        for (a, b), off in zip(self.keep, self.offsets):
            if t < a:
                return off  # caiu num trecho cortado: gruda no começo do próximo
            if t <= b:
                return off + (t - a)
        return self.duration

    def to_src(self, tau: float) -> float:
        for (a, b), off in zip(self.keep, self.offsets):
            if tau <= off + (b - a):
                return a + (tau - off)
        return self.keep[-1][1] if self.keep else tau

    @property
    def trimmed(self) -> bool:
        return len(self.keep) > 1 or (self.keep and self.keep[0][0] > 0.01)
