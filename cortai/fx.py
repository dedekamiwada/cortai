"""Filtros de cor, efeitos de vídeo e efeitos de voz do Estúdio.

Os filtros de cor são matrizes afins de cor (3 linhas x [r, g, b, deslocamento]). A prévia usa a
mesma matriz num feColorMatrix (SVG) e a exportação vira um LUT 3D (.cube) para o filtro lut3d do
ffmpeg, então prévia e vídeo final batem. A intensidade mistura a matriz com a identidade.
"""
from __future__ import annotations

import math
from pathlib import Path

LUM = (0.2126, 0.7152, 0.0722)

# ---------------------------------------------------------------- matrizes de cor
Matrix = list[list[float]]  # 3 x 4


def _ident() -> Matrix:
    return [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0]]


def _mul(a: Matrix, b: Matrix) -> Matrix:
    """a depois de b (afim): a(b(x))."""
    out = []
    for i in range(3):
        row = [sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
        row.append(sum(a[i][k] * b[k][3] for k in range(3)) + a[i][3])
        out.append(row)
    return out


def _sat(s: float) -> Matrix:
    return [[(1 - s) * LUM[j] + (s if i == j else 0) for j in range(3)] + [0.0] for i in range(3)]


def _contrast(c: float) -> Matrix:
    return [[c if i == j else 0.0 for j in range(3)] + [0.5 * (1 - c)] for i in range(3)]


def _bright(b: float) -> Matrix:
    return [[1.0 if i == j else 0.0 for j in range(3)] + [b] for i in range(3)]


def _temp(t: float, tint: float = 0.0) -> Matrix:
    """t > 0 esquenta (mais vermelho, menos azul); tint > 0 puxa para o verde, < 0 para o magenta."""
    return [[1 + t, 0, 0, 0], [0, 1 + tint, 0, 0], [0, 0, 1 - t, 0]]


def _hue(deg: float) -> Matrix:
    """Mesma rotação de matiz do feColorMatrix type=hueRotate."""
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    m = [
        [0.213 + c * 0.787 - s * 0.213, 0.715 - c * 0.715 - s * 0.715, 0.072 - c * 0.072 + s * 0.928],
        [0.213 - c * 0.213 + s * 0.143, 0.715 + c * 0.285 + s * 0.140, 0.072 - c * 0.072 - s * 0.283],
        [0.213 - c * 0.213 - s * 0.787, 0.715 - c * 0.715 + s * 0.715, 0.072 + c * 0.928 + s * 0.072],
    ]
    return [row + [0.0] for row in m]


def _sepia(amt: float) -> Matrix:
    sep = [[0.393, 0.769, 0.189], [0.349, 0.686, 0.168], [0.272, 0.534, 0.131]]
    return [[(1 - amt) * (1.0 if i == j else 0.0) + amt * sep[i][j] for j in range(3)] + [0.0] for i in range(3)]


def _fade(f: float) -> Matrix:
    """Levanta os pretos (aspecto de filme desbotado)."""
    return [[(1 - f) if i == j else 0.0 for j in range(3)] + [f] for i in range(3)]


def _chain(*ms: Matrix) -> Matrix:
    out = _ident()
    for m in ms:  # aplicadas na ordem em que aparecem
        out = _mul(m, out)
    return out


LOOKS: dict[str, tuple[str, Matrix]] = {
    "cinema": ("Cinema", _chain(_contrast(1.12), _sat(0.85), _temp(0.05), _fade(0.03))),
    "tealorange": ("Teal & Orange", _chain(_hue(-6), _sat(1.18), _temp(0.1, -0.02), _contrast(1.1))),
    "terror": ("Terror", _chain(_sat(0.45), _temp(-0.08, 0.05), _contrast(1.22), _bright(-0.06))),
    "noir": ("Noir", _chain(_sat(0), _contrast(1.35), _bright(-0.03))),
    "pb": ("P&B suave", _chain(_sat(0), _contrast(0.95), _fade(0.05))),
    "vintage": ("Vintage", _chain(_sepia(0.35), _sat(0.8), _contrast(0.92), _fade(0.08))),
    "sepia": ("Sépia", _sepia(1.0)),
    "quente": ("Quente", _chain(_temp(0.12), _sat(1.05))),
    "frio": ("Frio", _chain(_temp(-0.12, -0.02))),
    "dourado": ("Pôr do sol", _chain(_temp(0.18, 0.02), _sat(1.1), _bright(0.02))),
    "vivo": ("Vivo", _chain(_sat(1.45), _contrast(1.08))),
    "sonho": ("Sonhador", _chain(_sat(0.75), _fade(0.1), _bright(0.04))),
    "desbotado": ("Desbotado", _chain(_sat(0.6), _fade(0.12))),
    "cyberpunk": ("Cyberpunk", _chain(_temp(-0.15, -0.12), _sat(1.3), _contrast(1.1))),
    "matrix": ("Matrix", _chain(_temp(-0.1, 0.2), _sat(0.7), _contrast(1.15))),
    "vhs": ("VHS", _chain(_hue(8), _sat(1.2), _temp(0.0, -0.03), _contrast(0.9), _fade(0.06))),
}


def look_matrix(it: dict) -> Matrix | None:
    """Matriz final do item (filtro escolhido x intensidade), ou None sem filtro."""
    look = LOOKS.get(str(it.get("look") or ""))
    if not look:
        return None
    amt = max(0.0, min(1.0, float(it.get("look_amt", 1.0) if it.get("look_amt") is not None else 1.0)))
    if amt < 0.01:
        return None
    m, ident = look[1], _ident()
    return [[ident[i][j] + amt * (m[i][j] - ident[i][j]) for j in range(4)] for i in range(3)]


def write_cube(m: Matrix, path: Path, size: int = 17) -> None:
    """LUT 3D no formato .cube (vermelho varia mais rápido), com o mesmo corte em 0–1 da prévia."""
    lines = ["TITLE \"cortai\"", f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    n = size - 1
    for bi in range(size):
        for gi in range(size):
            for ri in range(size):
                x = (ri / n, gi / n, bi / n)
                out = [min(1.0, max(0.0, sum(m[i][k] * x[k] for k in range(3)) + m[i][3])) for i in range(3)]
                lines.append(f"{out[0]:.6f} {out[1]:.6f} {out[2]:.6f}")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


# ---------------------------------------------------------------- efeitos de vídeo
# cada efeito vai de 0 (desligado) a 1 (máximo); as fórmulas são as mesmas da prévia (studio.js)
EFFECTS = {
    "shake": "Tremor", "rgb": "Separação RGB", "grain": "Grão de filme", "vignette": "Vinheta",
    "blur": "Desfoque", "glow": "Brilho (glow)", "pixel": "Pixelado",
}
SHAKE_ZOOM = 1.08
VIGNETTE_START = 0.35


def effect(it: dict, name: str) -> float:
    try:
        return max(0.0, min(1.0, float((it.get("fx") or {}).get(name) or 0)))
    except (TypeError, ValueError, AttributeError):
        return 0.0


def shake_expr(axis: str, amt: float) -> str:
    """Deslocamento -1..1 do tremor em função do tempo (igual ao shakeAt do studio.js)."""
    if axis == "x":
        return f"({amt:.3f}*(0.6*sin(t*23+1)+0.4*sin(t*41)))"
    return f"({amt:.3f}*(0.6*sin(t*31)+0.4*sin(t*47+2)))"


def vignette_png(path: Path, W: int, H: int, amt: float) -> None:
    """Máscara da vinheta: preto com transparência em elipse, igual ao radial-gradient da prévia
    (farthest-corner: a elipse de 100% passa pelos cantos)."""
    import cv2
    import numpy as np
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    dx = (xx + 0.5 - W / 2) / (W / 2 * math.sqrt(2))
    dy = (yy + 0.5 - H / 2) / (H / 2 * math.sqrt(2))
    d = np.sqrt(dx * dx + dy * dy)
    alpha = np.clip((d - VIGNETTE_START) / (1 - VIGNETTE_START), 0, 1) * amt
    img = np.zeros((H, W, 4), np.uint8)
    img[..., 3] = (alpha * 255).round().astype(np.uint8)
    ok, buf = cv2.imencode(".png", img)
    path.write_bytes(buf.tobytes())


def main_fx(lin: str, lout: str, it: dict, W: int, H: int, tag: str, out_dir: Path, add_image) -> str:
    """Filtro de cor + efeitos para um item da faixa principal (quadro já no tamanho do vídeo).
    `add_image(path)` adiciona uma imagem parada como entrada e devolve o índice dela."""
    steps: list[str] = []  # pedaços do grafo; cada um lê [cur] e escreve [next]
    cur = lin
    n = 0

    def step(body: str) -> None:
        nonlocal cur, n
        n += 1
        nxt = f"{tag}x{n}"
        steps.append(body.replace("{in}", f"[{cur}]").replace("{out}", f"[{nxt}]"))
        cur = nxt

    m = look_matrix(it)
    if m:
        cube = out_dir / f"look-{tag}.cube"
        write_cube(m, cube)
        step(f"{{in}}lut3d=file={cube.name}:interp=trilinear{{out}}")
    blur = effect(it, "blur")
    if blur:
        step(f"{{in}}gblur=sigma={blur * 25:.2f}{{out}}")
    glow = effect(it, "glow")
    if glow:
        # o "screen" precisa ser em RGB (em YUV o croma sairia errado)
        step(f"{{in}}format=gbrp,split=2[{tag}ga][{tag}gb];[{tag}gb]gblur=sigma=20[{tag}gc];"
             f"[{tag}ga][{tag}gc]blend=all_mode=screen:all_opacity={glow:.3f},format=yuv420p{{out}}")
    pixel = effect(it, "pixel")
    if pixel:
        block = 2 + pixel * 38
        sw, sh = max(2, int(W / block)), max(2, int(H / block))
        step(f"{{in}}scale={sw}:{sh}:flags=area,scale={W}:{H}:flags=neighbor{{out}}")
    rgb = effect(it, "rgb")
    if rgb:
        d = max(1, round(rgb * 24))
        step(f"{{in}}rgbashift=rh={-d}:bh={d}:edge=smear{{out}}")
    vig = effect(it, "vignette")
    if vig:
        png = out_dir / f"vinheta-{tag}.png"
        vignette_png(png, W, H, vig)
        idx = add_image(png)
        step(f"[{idx}:v]format=rgba[{tag}vm];{{in}}[{tag}vm]overlay=0:0:format=auto:shortest=1{{out}}")
    grain = effect(it, "grain")
    if grain:
        step(f"{{in}}noise=alls={grain * 40:.1f}:allf=t+u{{out}}")
    shake = effect(it, "shake")
    if shake:
        sw, sh = _even(W * SHAKE_ZOOM), _even(H * SHAKE_ZOOM)
        step(f"{{in}}scale={sw}:{sh},crop={W}:{H}:x='(iw-ow)/2*(1+{shake_expr('x', shake)})':"
             f"y='(ih-oh)/2*(1+{shake_expr('y', shake)})'{{out}}")
    if not steps:
        return f"[{lin}]null[{lout}]"
    steps[-1] = steps[-1][: steps[-1].rindex(f"[{cur}]")] + f"[{lout}]"
    return ";".join(steps)


def _even(v: float) -> int:
    return max(2, int(round(v)) // 2 * 2)


# ---------------------------------------------------------------- voz
VOICES = {
    "": "Nenhum",
    "esquilo": "Esquilo (fina)",
    "grossa": "Voz grossa",
    "robo": "Robô",
    "telefone": "Telefone",
    "megafone": "Megafone",
    "eco": "Eco",
}


def voice_chain(it: dict) -> list[str]:
    """Filtros de áudio da limpeza de voz e do efeito escolhido (mantêm a duração)."""
    out: list[str] = []
    if it.get("voice_clean"):
        out += ["highpass=f=80", "afftdn=nf=-30", "acompressor=threshold=-20dB:ratio=3:attack=5:release=150",
                "dynaudnorm=f=200:g=15:p=0.9"]
    fx = str(it.get("voice_fx") or "")
    if fx == "esquilo":
        out += ["aresample=48000", "asetrate=72000", "aresample=48000", "atempo=0.6667"]
    elif fx == "grossa":
        out += ["aresample=48000", "asetrate=36000", "aresample=48000", "atempo=1.3333"]
    elif fx == "robo":
        out += ["afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':win_size=512:overlap=0.75"]
    elif fx == "telefone":
        out += ["highpass=f=400", "lowpass=f=3200", "acompressor=threshold=-24dB:ratio=6", "volume=1.4"]
    elif fx == "megafone":
        out += ["highpass=f=600", "lowpass=f=3000", "acrusher=bits=8:mode=log:mix=0.35", "volume=1.6"]
    elif fx == "eco":
        out += ["aecho=0.8:0.7:140|280:0.35|0.18"]
    return out


def catalog() -> dict:
    """Para a interface: nomes e matrizes dos filtros, efeitos e vozes."""
    return {
        "looks": [{"id": k, "label": v[0], "matrix": v[1]} for k, v in LOOKS.items()],
        "effects": [{"id": k, "label": v} for k, v in EFFECTS.items()],
        "voices": [{"id": k, "label": v} for k, v in VOICES.items()],
        "keys": [{"id": k, "label": v} for k, v in KEYS.items()],
        "blends": [{"id": k, "label": v[0]} for k, v in BLENDS.items()],
        "shake_zoom": SHAKE_ZOOM, "vignette_start": VIGNETTE_START,
    }


# ---------------------------------------------------------------- transições próprias
# Chicote, zoom, giro e glitch trocam de vídeo no meio da transição: o fim do vídeo que sai e o começo
# do que entra (metade da duração cada) recebem um filtro animado pelo tempo local t (0..h) e são
# emendados. Luz vazando é um esmaecer com um clarão quente atravessando o quadro.
CUSTOM_TRANSITIONS = {
    "whip": "Chicote", "zoomblur": "Zoom com desfoque", "spin": "Giro", "glitch": "Glitch", "lightleak": "Luz vazando",
}
CUT_TRANSITIONS = ("whip", "zoomblur", "spin", "glitch")


def cut_chain(name: str, side: str, h: float, W: int, H: int) -> str:
    """Filtro da metade `side` ("out" = fim do vídeo que sai, "in" = começo do que entra).
    Mesmas curvas da prévia (customTransition no studio.js): q vai de 0 a 1 no sentido do corte."""
    q = f"(t/{h:.4f})" if side == "out" else f"(1-t/{h:.4f})"
    tail = "format=yuv420p,setsar=1"
    if name == "zoomblur":
        z = f"(1+1.5*{q}*{q})"
        return (f"scale=w='trunc(iw*{z}/2)*2':h='trunc(ih*{z}/2)*2':eval=frame,crop={W}:{H},"
                f"tmix=frames=3,{tail}")
    if name == "spin":
        a = f"PI*{q}*{q}" if side == "out" else f"-PI*{q}*{q}"
        return f"rotate=a='{a}':c=black,tmix=frames=3,{tail}"
    if name == "whip":  # sai acelerando para a esquerda; o próximo entra pela direita freando
        if side == "out":
            return f"pad=iw*2.5:ih:0:0:black,crop={W}:{H}:x='iw*0.6*{q}*{q}':y=0,tmix=frames=4,{tail}"
        return f"pad=iw*2.5:ih:iw*1.5:0:black,crop={W}:{H}:x='iw*0.6-iw*0.6*{q}*{q}':y=0,tmix=frames=4,{tail}"
    if name == "glitch":
        return (f"pad=iw*1.3:ih:iw*0.15:0,crop={W}:{H}:x='iw*(0.115+0.1*sin(t*97)*gt(sin(t*53),0.2))':y=0,"
                f"rgbashift=rh=-14:bh=14:edge=smear,hue=h='90*gt(sin(t*71),0.5)':s='1+gt(sin(t*37),0.3)',"
                f"noise=alls=18:allf=t,{tail}")
    return tail


def leak_png(path: Path, W: int, H: int) -> None:
    """Clarão laranja com bordas suaves (atravessa o quadro na transição Luz vazando)."""
    import cv2
    import numpy as np
    w = W
    yy, xx = np.mgrid[0:H, 0:w].astype(np.float32)
    d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - H * 0.35) / (H * 0.6)) ** 2)
    alpha = np.clip(1 - d, 0, 1) ** 1.6 * 0.85
    img = np.zeros((H, w, 4), np.uint8)
    img[..., 0], img[..., 1], img[..., 2] = 40, 150, 255  # BGR: laranja
    img[..., 3] = (alpha * 255).astype(np.uint8)
    ok, buf = cv2.imencode(".png", img)
    path.write_bytes(buf.tobytes())


# ---------------------------------------------------------------- fundo verde e mesclagem (sobreposições)
KEYS = {"": "Nenhum", "green": "Verde", "blue": "Azul", "black": "Preto", "white": "Branco", "custom": "Outra cor"}
KEY_COLORS = {"green": "#00FF00", "blue": "#0000FF", "black": "#000000", "white": "#FFFFFF"}
BLENDS = {  # modo -> (nome, filtro blend do ffmpeg, cor neutra: onde a sobreposição não muda nada)
    "": ("Normal", None, None),
    "screen": ("Tela (clarear)", "screen", "black"),
    "addition": ("Somar", "addition", "black"),
    "lighten": ("Clarear", "lighten", "black"),
    "multiply": ("Multiplicar", "multiply", "white"),
    "darken": ("Escurecer", "darken", "white"),
    "overlay": ("Sobrepor", "overlay", "0x808080"),
    "difference": ("Diferença", "difference", "black"),
}


def key_filter(it: dict) -> str | None:
    """Recorte de fundo: chromakey (verde/azul/cor, em YUV) ou colorkey (preto/branco)."""
    key = str(it.get("key") or "")
    if key not in KEYS or not key:
        return None
    color = (it.get("key_color") if key == "custom" else KEY_COLORS.get(key)) or "#00FF00"
    hexc = "0x" + color.lstrip("#")[:6]
    sim = max(0.01, min(0.9, float(it.get("key_sim", 0.3 if key not in ("black", "white") else 0.12))))
    soft = max(0.0, min(0.5, float(it.get("key_blend", 0.1))))
    if key in ("black", "white"):
        return f"colorkey=color={hexc}:similarity={sim:.3f}:blend={soft:.3f}"
    out = f"chromakey=color={hexc}:similarity={sim:.3f}:blend={soft:.3f}"
    if key in ("green", "blue"):
        out += f",despill=type={key}:mix=0.5:expand=0.1"
    return out


def blend_mode(it: dict) -> tuple[str, str] | None:
    b = BLENDS.get(str(it.get("blend") or ""))
    return (b[1], b[2]) if b and b[1] else None
