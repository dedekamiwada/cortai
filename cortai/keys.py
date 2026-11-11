"""Keyframes do Estúdio: valores que mudam ao longo do tempo de um item.

Um item guarda `kf = {propriedade: [[t, valor], ...]}`, com t em segundos desde o começo do item
(na timeline), e `kf_ease` = "linear" ou "smooth" (suave: acelera e freia). Antes da primeira chave
vale o primeiro valor; depois da última, o último. A prévia (studio.js) usa as mesmas contas.
"""
from __future__ import annotations

PROPS = {
    "main": ("zoom", "pan_x", "pan_y", "volume"),
    "overlay": ("x", "y", "scale", "opacity", "rotate", "volume"),
    "text": ("x", "y", "scale", "opacity"),
    "audio": ("volume",),
}


def keys(it: dict, prop: str) -> list[tuple[float, float]]:
    raw = (it.get("kf") or {}).get(prop) or []
    out = []
    for k in raw:
        try:
            out.append((max(0.0, float(k[0])), float(k[1])))
        except (TypeError, ValueError, IndexError):
            continue
    out.sort()
    return out


def animated(it: dict, prop: str) -> bool:
    return len(keys(it, prop)) >= 1


def smooth(it: dict) -> bool:
    return (it.get("kf_ease") or "linear") == "smooth"


def value_at(it: dict, prop: str, t: float, default: float) -> float:
    ks = keys(it, prop)
    if not ks:
        return default
    if t <= ks[0][0]:
        return ks[0][1]
    for (t0, v0), (t1, v1) in zip(ks, ks[1:]):
        if t < t1:
            u = (t - t0) / (t1 - t0) if t1 > t0 else 1.0
            if smooth(it):
                u = u * u * (3 - 2 * u)
            return v0 + (v1 - v0) * u
    return ks[-1][1]


def expr(it: dict, prop: str, default: float, tvar: str = "t", offset: float = 0.0, scale: float = 1.0) -> str:
    """Expressão do ffmpeg para o valor no tempo `tvar` (menos `offset`), vezes `scale`."""
    ks = keys(it, prop)
    if not ks:
        return f"{default * scale:.5f}"
    L = f"({tvar}-{offset:.4f})" if offset else f"({tvar})"
    out = f"{ks[-1][1] * scale:.5f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(ks, ks[1:]))):
        span = max(1e-4, t1 - t0)
        u = f"clip(({L}-{t0:.4f})/{span:.4f},0,1)"
        if smooth(it):
            u = f"({u}*{u}*(3-2*{u}))"
        seg = f"({v0 * scale:.5f}+({(v1 - v0) * scale:.5f})*{u})"
        out = f"if(lt({L},{t1:.4f}),{seg},{out})"
    return f"if(lt({L},{ks[0][0]:.4f}),{ks[0][1] * scale:.5f},{out})"


def extreme(it: dict, prop: str, default: float, fn=max) -> float:
    ks = keys(it, prop)
    return fn([v for _, v in ks]) if ks else default


def times(it: dict, props: tuple[str, ...]) -> list[float]:
    """Todos os tempos de chave do item (para dividir os eventos de texto)."""
    return sorted({t for p in props for t, _ in keys(it, p)})
