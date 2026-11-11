"""Leva um clipe do Cortaí para o Estúdio do jeito que ele foi renderizado.

- Cortes de pausa (jump cuts) e zooms de reação viram pedaços da faixa principal: cada zoom
  é um pedaço com "Zoom" e "Posição" mirando o rosto, que dá para mudar ou desfazer no Estúdio.
- Cada bloco de legenda vira um texto editável, com a palavra falada em destaque (karaokê),
  palavras-chave coloridas, mesma fonte, tamanho, contorno e posição do clipe.
- O título na tela (se ligado) vira um texto com caixa de fundo.
"""
from __future__ import annotations

from pathlib import Path

from . import captions, reframe, render
from .edit import TimeMap

W, H = render.OUT_W, render.OUT_H
MIN_PIECE = 0.04  # pedaços menores que isso grudam no vizinho


def _hex(ass: str | None) -> str:
    """&HBBGGRR& (ASS) -> #RRGGBB."""
    h = (ass or "").replace("&H", "").replace("&", "")[-6:]
    return f"#{h[4:6]}{h[2:4]}{h[0:2]}".upper() if len(h) == 6 else "#FFFFFF"


def _pan(cx: float, cy: float, zoom: float, src_w: int, src_h: int, top: float = 0.5) -> tuple[float, float]:
    """pan_x/pan_y do Estúdio (-1..1) para o recorte "Preencher" centrar em (cx, cy) do vídeo original.

    O Estúdio escala o vídeo para cobrir W*zoom x H*zoom e recorta W x H a partir de
    (iw - W) / 2 * (1 + pan). `top` é onde o ponto fica na altura do quadro (0,4 = um pouco
    acima do meio, como o zoom de reação do render)."""
    s = max(W * zoom / src_w, H * zoom / src_h)
    iw, ih = src_w * s, src_h * s

    def axis(c: float, full: float, view: int, lead: float) -> float:
        if full - view < 1:
            return 0.0
        x0 = c * full - lead * view
        return round(max(-1.0, min(1.0, 2 * x0 / (full - view) - 1)), 3)

    return axis(cx, iw, W, 0.5), axis(cy, ih, H, top)


def _framing(job: dict, clip: dict, opts: dict, zooms: list[float]) -> dict:
    """Como o clipe foi enquadrado: modo do Estúdio, centro do quadro e mira do zoom."""
    src = job["source"]
    w, h = src["width"], src["height"]
    start, dur = clip["start"], clip["end"] - clip["start"]
    landscape = w / h > 0.6
    layout = opts.get("layout") or "auto"
    crop_x = min(1.0, max(0.0, float(opts.get("crop_x", 0.5))))
    face, face_ratio = None, 0.0
    if zooms or (landscape and layout in ("auto", "face")):
        boxes = reframe.face_positions(Path(src["path"]), start, dur, w, h)
        face_ratio = sum(b is not None for b in boxes) / len(boxes) if boxes else 0.0
        face = reframe.face_box(boxes)
    if not landscape:
        fit = "fill" if abs(w / h - W / H) < 0.03 else "blur"
        center = (0.5, 0.5)
    else:
        if layout == "auto":
            layout = "face" if face_ratio >= 0.35 else "fit"
        if layout == "face" and face:
            fit, center = "fill", (face[0], 0.5)
        elif layout in ("manual", "split"):  # tela dividida não existe no Estúdio: fica o lado escolhido
            fit, center = "fill", (crop_x, 0.5)
        else:
            fit, center = "blur", (0.5, 0.5)
    target = (face[0], face[1]) if face else center
    return {"fit": fit, "center": center, "target": target, "w": w, "h": h}


def main_items(job: dict, clip: dict, opts: dict, keep, zooms: list[float], seg_start: float,
               asset_id: str) -> tuple[list[dict], TimeMap]:
    """Pedaços da faixa principal (tempo do trecho recortado) e o mapa de tempo do clipe final."""
    start, dur = clip["start"], clip["end"] - clip["start"]
    tm = TimeMap(keep or [(0.0, dur)])
    out_dur = tm.duration
    windows = []
    for z in sorted(zooms or []):  # mesma regra do render: um zoom por vez, com respiro entre eles
        t = tm.to_out(z - start)
        if 0 <= t < out_dur - 0.3 and all(abs(t - p) > render.ZOOM_HOLD + 1.0 for p, _ in windows):
            windows.append((t, min(out_dur, t + render.ZOOM_HOLD)))
    fr = _framing(job, clip, opts, zooms if windows else [])
    if fr["fit"] != "fill":
        windows = []  # "Encaixar"/"Fundo desfocado" do Estúdio não têm zoom
    base_pan = _pan(*fr["center"], 1.0, fr["w"], fr["h"])
    zoom_pan = _pan(*fr["target"], render.ZOOM, fr["w"], fr["h"], top=0.4)

    items = []
    for (a, b), off in zip(tm.keep, tm.offsets):
        length = b - a
        cuts = [0.0, length]
        for z0, z1 in windows:
            for t in (z0 - off, z1 - off):
                if MIN_PIECE < t < length - MIN_PIECE:
                    cuts.append(t)
        cuts = sorted(set(round(c, 3) for c in cuts))
        for t0, t1 in zip(cuts, cuts[1:]):
            mid = off + (t0 + t1) / 2
            zoomed = any(z0 <= mid < z1 for z0, z1 in windows)
            pan = zoom_pan if zoomed else base_pan
            items.append({
                "id": f"i{asset_id}{len(items):02d}", "asset": asset_id,
                "in": round(start + a + t0 - seg_start, 3), "out": round(start + a + t1 - seg_start, 3),
                "speed": 1, "volume": 1, "fit": fr["fit"],
                "zoom": render.ZOOM if zoomed else 1, "pan_x": pan[0], "pan_y": pan[1],
            })
    return items, tm


def caption_items(clip: dict, opts: dict, words: list[dict], tm: TimeMap) -> list[dict]:
    """Um texto por bloco de legenda, com o tempo de cada palavra para o destaque (karaokê)."""
    if not opts.get("captions", True):
        return []
    start, end = clip["start"], clip["end"]
    out_dur = tm.duration
    inside = [x for x in captions.apply_fixes(words, opts.get("word_fixes"), opts.get("word_adds"))
              if x["e"] > start and x["s"] < end]
    mapped = [{**x, "s": tm.to_out(x["s"] - start), "e": tm.to_out(x["e"] - start)} for x in inside]
    preset = captions.PRESETS.get(opts.get("caption_preset") or "destaque", captions.PRESETS["destaque"])
    align, margin = captions.POSITIONS.get(opts.get("caption_position") or "bottom", captions.POSITIONS["bottom"])
    size = preset["size"]
    y = {2: (H - margin - size * 0.6) / H, 5: 0.5, 8: (margin + size * 0.6) / H}[align]
    upper = bool(opts.get("caption_upper", True))
    keys = captions._keyword_set(opts.get("keywords")) if opts.get("keyword_highlight", True) else set()
    chunks = captions._chunks(mapped, preset["max_words"], preset["max_chars"])
    items = []
    for ci, chunk in enumerate(chunks):
        next_start = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else out_dur
        t0 = chunk[0]["s"]
        spans = []
        for i, w in enumerate(chunk):  # mesmos tempos do captions.build_ass
            ws = w["s"]
            we = chunk[i + 1]["s"] if i + 1 < len(chunk) else min(next_start, w["e"] + 0.35)
            if we <= ws:
                we = ws + 0.08
            spans.append([round(ws - t0, 3), round(we - t0, 3)])
        dur = min(spans[-1][1], out_dur - t0)
        if dur < 0.05:
            continue
        labels = [w["w"].upper() if upper else w["w"] for w in chunk]
        it = {
            "id": f"tcap{ci:03d}", "start": round(t0, 3), "dur": round(dur, 3), "text": " ".join(labels),
            "font": preset["font"], "size": size, "color": "#FFFFFF", "stroke": "#000000",
            "stroke_w": preset["outline"], "bg": "", "x": 0.5, "y": round(y, 4), "anim": "none", "bold": True,
            "words": spans, "hl_mode": preset["mode"], "hl": (opts.get("caption_color") or "#FFE600").upper(),
            "hl_pop": preset["pop"],
        }
        kw = [i for i, w in enumerate(chunk) if captions._norm(w["w"]) in keys]
        if kw:
            it.update(kw_idx=kw, kw_color=(opts.get("keyword_color") or "#39E75F").upper())
        items.append(it)
    return items


def title_item(clip: dict, opts: dict, out_dur: float) -> dict | None:
    text = (opts.get("title_text") or clip.get("title") or "").strip()
    if not opts.get("title_overlay") or not text:
        return None
    style = captions.TITLE_STYLES.get(opts.get("title_style") or "impacto", captions.TITLE_STYLES["impacto"])
    lines, size = captions._title_lines(text.upper(), style)
    words = [w for line in lines for w in line.split()]
    it = {
        "id": "ttitle", "start": 0, "text": "\n".join(lines), "font": style["font"], "size": size,
        "color": _hex(style["text"]), "stroke": "#000000", "stroke_w": style["outline"], "bg": _hex(style["bar"]),
        "x": 0.5, "y": round(captions.TITLE_POSITIONS.get(opts.get("title_position") or "top", 230) / H, 4),
        "bold": True,
    }
    hook = (opts.get("title_mode") or "always") == "hook"
    it.update(dur=round(min(out_dur, captions.HOOK_SECONDS) if hook else out_dur, 3), anim="pop" if hook else "fade")
    if style["highlight"] and words:  # palavra-chave do clipe em destaque; sem ela, a mais longa
        keys = captions._keyword_set(opts.get("keywords"))
        marked = [i for i, w in enumerate(words) if captions._norm(w) in keys]
        if not marked:
            marked = [max(range(len(words)), key=lambda i: len(captions._norm(words[i])))]
        it.update(kw_idx=marked, kw_color=_hex(style["highlight"]))
    return it


def build(job: dict, clip: dict, opts: dict, words: list[dict], keep, zooms: list[float],
          seg_start: float, asset_id: str) -> dict:
    """Faixas principal e de texto do projeto do Estúdio para este clipe."""
    main, tm = main_items(job, clip, opts, keep, zooms, seg_start, asset_id)
    text = caption_items(clip, opts, words, tm)
    title = title_item(clip, opts, tm.duration)
    if title:
        text.append(title)
    return {"main": main, "text": text}
