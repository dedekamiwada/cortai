"""Capa (thumbnail) de cada clipe: um quadro do momento ápice com o título por cima, em 1080x1920.

- Quadros: o render tira 2 quadros por segundo do clipe SEM legenda nem título (pasta capa/quadros);
  projetos antigos usam o vídeo original (lives verticais) ou o clipe pronto.
- Ápice: a reação do rosto mais perto logo depois do pico que o Claude marcou; sem isso, o pico;
  sem pico, o som mais alto do trecho. Entre os quadros vizinhos fica o mais nítido.
- Desenho: ffmpeg + libass (o mesmo motor do título e das legendas), com as fontes de static/fonts.
  Sem Pillow: a versão portátil exclui o PIL de propósito.
- Zona segura do TikTok: o perfil mostra a capa cortada em ~3:4 no meio e o título automático
  cobre a parte de baixo, então o texto fica entre 18% e 80% da altura.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import uuid
from pathlib import Path

import cv2
import numpy as np

from . import paths

W, H = 1080, 1920
FRAME_STEP = 0.5            # segundos entre os quadros guardados para escolher a capa
FRAME_W, FRAME_H = 720, 1280
SAFE_TOP, SAFE_BOTTOM = 0.18, 0.80
MAX_LINES = 3
TEXT_W = 960                # largura máxima de uma linha
IN_VIDEO_SECONDS = 0.4      # capa no fim do vídeo, para escolher como capa no TikTok

# Cores em hex RGB (o ASS usa BGR, ver _ass). Campos opcionais:
#   oc = cor do contorno · outer = (cor, largura) 2º contorno por fora (adesivo) · glow = (cor, largura, borrão)
#   bar = faixa atrás de cada linha (bar_fax = inclinação) · box/hl_box = caixa opaca atrás das palavras
#   mark/mark_text = marca-texto só atrás das palavras destacadas · shadow = sombra projetada
A, B, M = "Anton-Regular.ttf", "Bangers-Regular.ttf", "Montserrat-Black.ttf"
STYLES = {
    "impacto": {"label": "Impacto (Anton, contorno)", "font": A, "size": 220, "lh": 1.02,
                "text": "#FFFFFF", "hl": "#FFD400", "outline": 11},
    "adesivo": {"label": "Adesivo (contorno duplo vermelho)", "font": A, "size": 210, "lh": 1.08,
                "text": "#FFFFFF", "hl": "#FFD400", "outline": 10, "outer": ("#E61E28", 14)},
    "neon": {"label": "Neon vermelho (brilho)", "font": A, "size": 210, "lh": 1.05,
             "text": "#FFFFFF", "hl": "#FFD400", "outline": 5, "oc": "#E61E28", "glow": ("#E61E28", 16, 14),
             "shadow": False},
    "grito": {"label": "Grito (vermelho e branco)", "font": A, "size": 235, "lh": 1.0,
              "text": "#E61E28", "hl": "#FFFFFF", "outline": 10, "oc": "#FFFFFF", "hl_oc": "#E61E28",
              "outer": ("#0A0A0C", 10)},
    "faixa": {"label": "Faixa vermelha (Bangers)", "font": B, "size": 215, "lh": 1.02,
              "text": "#FFFFFF", "hl": "#FFE14D", "outline": 7, "bar": "#E61E28", "bar_fax": -0.12},
    "fita": {"label": "Fita preta (Bangers)", "font": B, "size": 210, "lh": 1.04,
             "text": "#F5F5F5", "hl": "#E61E28", "outline": 0, "bar": "#0A0A0C", "bar_fax": 0.1},
    "quadrinho": {"label": "Quadrinho (amarelo)", "font": B, "size": 205, "lh": 1.12,
                  "text": "#0A0A0C", "hl": "#E61E28", "outline": 0, "box": "#FFD400", "hl_box": "#FFD400"},
    "marcador": {"label": "Marca-texto (Montserrat)", "font": M, "size": 145, "lh": 1.18,
                 "text": "#FFFFFF", "hl": "#0A0A0C", "outline": 8, "mark": "#FFD400"},
    "caixa": {"label": "Caixa branca (Montserrat)", "font": M, "size": 140, "lh": 1.32,
              "text": "#0A0A0C", "hl": "#FFFFFF", "outline": 0, "box": "#F5F5F5", "hl_box": "#E61E28"},
    "minimal": {"label": "Minimalista (sem contorno)", "font": M, "size": 130, "lh": 1.15,
                "text": "#FFFFFF", "hl": "#FFD400", "outline": 0, "soft": True},
}
DEFAULT_STYLE = "impacto"
PARAM_KEYS = ("text", "style", "upper", "size", "y", "tilt", "zoom", "px", "py", "dark", "handle",
              "color", "hl_color", "frame")

_metrics: dict | None = None
_fonts_dir: str | None = None


# ------------------------------------------------------------------ fontes e medidas
def _font_metrics() -> dict:
    global _metrics
    if _metrics is None:
        _metrics = json.loads((paths.STATIC / "fonts" / "metrics.json").read_text(encoding="utf-8"))
    return _metrics


def fonts_dir() -> str:
    """Pasta das fontes para o libass. No Windows ele não abre caminhos com acento
    (ex.: D:\\EdiçãoClaude), então copia as fontes para uma pasta temporária sem acento."""
    global _fonts_dir
    if _fonts_dir:
        return _fonts_dir
    src = paths.STATIC / "fonts"
    if str(src).isascii():
        _fonts_dir = str(src)
        return _fonts_dir
    for folder in (tempfile.gettempdir(), os.environ.get("PUBLIC", ""), "C:\\"):
        if not folder or not folder.isascii():
            continue
        try:
            dst = Path(folder) / "cortai_fontes"
            dst.mkdir(exist_ok=True)
            for f in src.glob("*.ttf"):
                target = dst / f.name
                if not target.exists() or target.stat().st_size != f.stat().st_size:
                    shutil.copyfile(f, target)
            _fonts_dir = str(dst)
            return _fonts_dir
        except OSError:
            pass
    _fonts_dir = str(src)  # sem saída: o libass cai numa fonte do sistema
    return _fonts_dir


def _filter_path(p: str) -> str:
    """Caminho dentro de um filtro do ffmpeg (C:\\x → 'C\\:/x')."""
    return "'" + p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'") + "'"


def text_width(text: str, font: str, size: float) -> float:
    m = _font_metrics()[font]
    widths = m["w"]
    fallback = widths.get("o", 0.55)
    return sum(widths.get(ch, fallback) for ch in text) * m["em"] * size


# ------------------------------------------------------------------ texto
def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in text if ch.isalnum())


def auto_highlight(title: str, keywords: list[str] | None) -> str:
    """Marca com *asteriscos* a palavra-chave do clipe que aparece no título; sem ela, a última palavra."""
    words = title.split()
    if not words or "*" in title:
        return title
    norm = [_norm(w) for w in words]
    for k in keywords or []:
        parts = [_norm(p) for p in str(k).split() if _norm(p)]
        if not parts:
            continue
        for i in range(len(norm) - len(parts) + 1):  # frase inteira
            if norm[i:i + len(parts)] == parts and len(parts) < len(words):
                return " ".join(f"*{w}*" if i <= j < i + len(parts) else w for j, w in enumerate(words))
    for k in keywords or []:  # mesma raiz ("corre" × "correndo")
        for p in str(k).split():
            p = _norm(p)
            if len(p) < 4:
                continue
            for j, n in enumerate(norm):
                if len(n) >= 4 and (n[:5] == p[:5] or n.startswith(p) or p.startswith(n)):
                    return " ".join(f"*{w}*" if i == j else w for i, w in enumerate(words))
    if len(words) >= 2:
        return " ".join(words[:-1]) + f" *{words[-1]}*"
    return title


def _tokens(text: str) -> list[list[tuple[str, bool]]]:
    """Linhas (o usuário pode quebrar com Enter) de palavras (texto, destacada). *assim* destaca."""
    lines = []
    for raw in text.replace("\r", "").split("\n"):
        out, hl = [], False
        for word in raw.split():
            opens, closes = word.startswith("*"), word.endswith("*") and (len(word) > 1 or hl)
            clean = word.strip("*")
            if clean:
                out.append((clean, hl or opens))
            hl = (hl or opens) and not closes
        if out:
            lines.append(out)
    return lines


def _wrap(lines, font: str, size: float) -> list[list[tuple[str, bool]]]:
    """Quebra cada linha no menor número de linhas que cabe e, com esse número, equilibra a
    largura (evita a última palavra sozinha)."""
    from itertools import combinations
    space = text_width(" ", font, size)
    out = []
    for line in lines:
        widths = [text_width(w, font, size) for w, _ in line]

        def width(a: int, b: int) -> float:
            return sum(widths[a:b]) + space * (b - a - 1)

        n = len(line)
        best = None
        for k in range(1, n + 1):
            for cuts in combinations(range(1, n), k - 1):
                bounds = (0, *cuts, n)
                worst = max(width(bounds[i], bounds[i + 1]) for i in range(k))
                if worst <= TEXT_W and (best is None or worst < best[0]):
                    best = (worst, bounds)
            if best or k >= 6 or (n > 14 and k >= 3):
                break
        if not best:  # palavra maior que a largura: uma por linha
            bounds = tuple(range(n + 1))
        else:
            bounds = best[1]
        out += [line[bounds[i]:bounds[i + 1]] for i in range(len(bounds) - 1)]
    return out


def layout_text(text: str, style: dict, scale: float, upper: bool) -> tuple[list, float]:
    """Quebra em até 3 linhas e acha o maior tamanho de fonte que cabe."""
    if upper:
        text = text.upper()
    lines = _tokens(text)
    if not lines:
        return [], 0
    font = style["font"]
    size = style["size"] * scale
    floor = size * 0.45
    while True:
        wrapped = _wrap(lines, font, size)
        widest = max(text_width(" ".join(w for w, _ in ln), font, size) for ln in wrapped)
        if (len(wrapped) <= max(MAX_LINES, len(lines)) and widest <= TEXT_W) or size <= floor:
            return wrapped, size
        size *= 0.94


# ------------------------------------------------------------------ ASS
def _ass(hex_color: str, alpha: int = 0) -> str:
    h = (hex_color or "#FFFFFF").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: T,{family},{size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,{border},{outline},0,5,0,0,0,1
Style: Shape,Arial,10,&H00000000,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Handle,Bangers,76,&H00F5F5F5,&H000000FF,&H000A0A0C,&H00000000,0,0,0,0,100,100,2,0,1,5,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _rect(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"m {x0:.0f} {y0:.0f} l {x1:.0f} {y0:.0f} l {x1:.0f} {y1:.0f} l {x0:.0f} {y1:.0f}"


def build_ass(p: dict) -> str:
    style = STYLES.get(p.get("style"), STYLES[DEFAULT_STYLE])
    font = style["font"]
    family = _font_metrics()[font]["family"]
    lines, size = layout_text(p.get("text") or "", style, float(p.get("size") or 1), p.get("upper", True))
    color = p.get("color") or style["text"]
    hl = p.get("hl_color") or style["hl"]
    box = style.get("box")
    outline = style["outline"]
    oc = _ass(style.get("oc") or "#000000")
    out = [HEADER.format(family=family, size=round(size), border=3 if box else 1,
                         outline=round(size * 0.16) if box else outline)]
    span = "0:00:00.00,0:00:10.00"
    if not lines:
        return "".join(out)
    lh = size * style["lh"]
    block_h = lh * len(lines)
    y_center = min(max(float(p.get("y", 0.5)), SAFE_TOP), SAFE_BOTTOM) * H
    y_center = min(max(y_center, SAFE_TOP * H + block_h / 2), SAFE_BOTTOM * H - block_h / 2)
    top = y_center - block_h / 2
    tilt = float(p.get("tilt") or 0)
    rot = f"\\frz{tilt:g}" if tilt else ""
    dark = min(max(float(p.get("dark", 0.35)), 0), 1)
    space = text_width(" ", font, size)

    if dark > 0:  # sombra suave atrás do bloco de texto, de lado a lado
        a = int(255 - 200 * dark)
        y0, y1 = top - lh * 0.6, top + block_h + lh * 0.6
        out.append(f"Dialogue: 0,{span},Shape,,0,0,0,,{{\\an7\\pos(0,0)\\c&H000000&\\alpha&H{a:02X}&\\blur60\\p1}}"
                   f"{_rect(-100, y0, 1180, y1)}{{\\p0}}\n")

    for i, line in enumerate(lines):
        cy = round(top + lh * (i + 0.5))
        widths = [text_width(w, font, size) for w, _ in line]
        line_w = sum(widths) + space * (len(line) - 1)
        shape = f"{{\\an7\\pos(540,{cy})\\org(540,{cy}){rot}"  # desenho em volta do centro da linha
        if style.get("bar"):  # faixa atrás de cada linha, como pincelada
            bw, bh = line_w + size * 0.5, size * 0.98
            out.append(f"Dialogue: 1,{span},Shape,,0,0,0,,{shape}\\c{_ass(style['bar'])}"
                       f"\\fax{style.get('bar_fax', 0):g}\\p1}}{_rect(-bw / 2, -bh / 2, bw / 2, bh / 2)}{{\\p0}}\n")
        if style.get("mark"):  # marca-texto atrás de cada trecho destacado
            x = 540 - line_w / 2
            run = None
            for k, ((word, on), w) in enumerate(zip(line, widths)):
                if on:
                    run = [run[0] if run else x, x + w]
                if (not on or k == len(line) - 1) and run:
                    pad = size * 0.12
                    out.append(f"Dialogue: 1,{span},Shape,,0,0,0,,{shape}\\c{_ass(style['mark'])}\\fax-0.06\\p1}}"
                               f"{_rect(run[0] - 540 - pad, -size * 0.42, run[1] - 540 + pad, size * 0.42)}{{\\p0}}\n")
                    run = None
                x += w + space
        plain = " ".join(_esc(w) for w, _ in line)
        if style.get("glow"):
            gc, gw, gb = style["glow"]
            out.append(f"Dialogue: 2,{span},T,,0,0,0,,{{\\pos(540,{cy}){rot}\\c{_ass(gc)}\\3c{_ass(gc)}"
                       f"\\bord{gw}\\blur{gb}\\alpha&H30&}}{plain}\n")
        if style.get("outer"):  # 2º contorno por fora (efeito adesivo)
            ocol, ow = style["outer"]
            out.append(f"Dialogue: 2,{span},T,,0,0,0,,{{\\pos(540,{cy}){rot}\\c{_ass(ocol)}\\3c{_ass(ocol)}"
                       f"\\bord{outline + ow}\\blur1}}{plain}\n")
        if style.get("soft"):  # minimalista: só uma sombra difusa
            out.append(f"Dialogue: 1,{span},T,,0,0,0,,{{\\pos(540,{cy + size * 0.04:.0f}){rot}\\c&H000000&"
                       f"\\3c&H000000&\\alpha&H40&\\bord6\\blur18}}{plain}\n")
        elif not box and style.get("shadow", True):  # sombra projetada para dar volume
            extra = style["outer"][1] if style.get("outer") else 0
            out.append(f"Dialogue: 1,{span},T,,0,0,0,,{{\\pos({540 + size * 0.05:.0f},{cy + size * 0.06:.0f}){rot}"
                       f"\\c&H000000&\\3c&H000000&\\alpha&H60&\\bord{outline + extra + 6}\\blur8}}{plain}\n")
        parts = []
        for word, on in line:
            if box:
                back = style.get("hl_box") if on else box
                parts.append(f"{{\\c{_ass(hl if on else color)}\\3c{_ass(back)}}}{_esc(word)}")
            elif style.get("mark"):  # palavra no marca-texto sem contorno
                parts.append(f"{{\\c{_ass(hl if on else color)}\\bord{0 if on else outline}}}{_esc(word)}")
            elif style.get("hl_oc"):  # destaque invertido (contorno de outra cor)
                parts.append(f"{{\\c{_ass(hl if on else color)}\\3c{_ass(style['hl_oc']) if on else oc}}}{_esc(word)}")
            else:
                parts.append(f"{{\\c{_ass(hl if on else color)}}}{_esc(word)}")
        out.append(f"Dialogue: 3,{span},T,,0,0,0,,{{\\pos(540,{cy}){rot}\\3c{oc}}}" + " ".join(parts) + "\n")

    handle = (p.get("handle") or "").strip()
    if handle:
        hy = top + block_h + lh * 0.25 + 40
        if hy > 0.87 * H:
            hy = top - lh * 0.25 - 40
        out.append(f"Dialogue: 4,{span},Handle,,0,0,0,,{{\\pos(540,{hy:.0f}){rot}}}{_esc(handle)}\n")
    return "".join(out)


# ------------------------------------------------------------------ imagem
def _read(path: Path):
    """cv2.imread não abre caminho com acento no Windows."""
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def render(frame: Path, params: dict, out: Path, preview: bool = False) -> Path:
    """Desenha a capa. preview=True sai em 540x960 (rápido, para o editor)."""
    img = _read(frame)
    if img is None:
        raise RuntimeError(f"quadro da capa não encontrado: {frame.name}")
    fh, fw = img.shape[:2]
    zoom = min(max(float(params.get("zoom") or 1), 1), 3)
    scale = max(W / fw, H / fh) * zoom
    sw, sh = max(W, round(fw * scale / 2) * 2), max(H, round(fh * scale / 2) * 2)
    px = min(max(float(params.get("px", 0.5)), 0), 1)
    py = min(max(float(params.get("py", 0.5)), 0), 1)
    cx, cy = round((sw - W) * px), round((sh - H) * py)
    work = out.parent
    work.mkdir(parents=True, exist_ok=True)
    ass_name = f"_capa_{uuid.uuid4().hex[:8]}.ass"
    (work / ass_name).write_text(build_ass(params), encoding="utf-8")
    vf = (f"scale={sw}:{sh}:flags=lanczos,crop={W}:{H}:{cx}:{cy},eq=saturation=1.15:contrast=1.06,"
          f"vignette=PI/5,subtitles={ass_name}:fontsdir={_filter_path(fonts_dir())}")
    if preview:
        vf += ",scale=540:960"
    tmp = out.with_name(f"_{uuid.uuid4().hex[:8]}{out.suffix}")
    try:
        proc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(frame), "-vf", vf, "-frames:v", "1",
                               "-q:v", "4" if preview else "2", tmp.name],
                              cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0 or not tmp.exists():
            raise RuntimeError(f"ffmpeg falhou ao desenhar a capa: {proc.stderr[-800:]}")
        os.replace(tmp, out)
    finally:
        (work / ass_name).unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
    return out


# ------------------------------------------------------------------ quadros e ápice
def frames_dir(clip_dir: Path) -> Path:
    return clip_dir / "capa" / "quadros"


def list_frames(clip_dir: Path) -> list[Path]:
    return sorted(frames_dir(clip_dir).glob("*.jpg"))


def extract_frames(src: Path, start: float, dur: float, clip_dir: Path, vertical_source: bool) -> str:
    """Para clipes renderizados antes da capa existir. Live vertical: tira do vídeo original (sem
    legenda). Horizontal: do clipe pronto (com o que estiver gravado nele). Devolve a origem usada."""
    d = frames_dir(clip_dir)
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    vf = f"fps={1 / FRAME_STEP:g},scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=increase,crop={FRAME_W}:{FRAME_H}"
    if vertical_source:
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}"]
        origin = "original"
    else:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", "clip.mp4"]
        origin = "clipe"
    subprocess.run(cmd + ["-vf", vf, "-q:v", "3", "-start_number", "0", "capa/quadros/%04d.jpg"],
                   cwd=clip_dir, capture_output=True)
    return origin


def apex_time(clip: dict, loud_events: list[dict] | None = None) -> float:
    """Segundo (do vídeo original) do momento ápice do clipe."""
    s, e = float(clip["start"]), float(clip["end"])
    peak = clip.get("peak")
    reacts = [float(r) for r in clip.get("reactions") or [] if s <= float(r) <= e]
    if peak is not None and s <= float(peak) <= e:
        peak = float(peak)
        after = [r for r in reacts if peak - 0.5 <= r <= peak + 6]
        if after:
            return min(after, key=lambda r: abs(r - peak - 0.8))
        return min(peak + 0.4, e - 0.1)
    if reacts:
        return reacts[0]
    loud = [ev for ev in loud_events or [] if s <= ev["t"] <= e]
    if loud:
        return max(loud, key=lambda ev: ev.get("db", 0))["t"]
    return s + (e - s) * 0.4


def _sharpness(path: Path) -> float:
    img = _read(path)
    if img is None:
        return 0.0
    gray = cv2.cvtColor(cv2.resize(img, (360, 640)), cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def best_frame(frames: list[Path], t_out: float) -> int:
    """Índice do quadro do ápice: o mais nítido entre o do segundo certo e os vizinhos
    (no pico o jogo costuma estar borrado de movimento)."""
    if not frames:
        return 0
    i = min(max(int(round(t_out / FRAME_STEP)), 0), len(frames) - 1)
    near = [j for j in (i, i + 1, i - 1, i + 2) if 0 <= j < len(frames)]
    scores = {j: _sharpness(frames[j]) for j in near}
    top = max(scores.values()) or 1
    # o quadro exato ganha empate: só troca se o vizinho for bem mais nítido
    return max(near, key=lambda j: scores[j] / top + (0.15 if j == i else 0))


def _face_y(frame: Path) -> float | None:
    """Altura (0..1) do maior rosto no quadro, para o texto não ficar em cima dele."""
    try:
        from .reframe import _cascade
        img = _read(frame)
        if img is None or _cascade.empty():
            return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces = _cascade.detectMultiScale(gray, 1.1, 5, minSize=(img.shape[1] // 12, img.shape[1] // 12))
        if len(faces) == 0:
            return None
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
        return (y + h / 2) / img.shape[0]
    except Exception:
        return None


def default_y(frame: Path, layout: str | None) -> float:
    fy = _face_y(frame)
    if fy is None:
        return 0.5 if layout == "split" else 0.42
    return 0.62 if fy < 0.5 else 0.3


def default_params(clip: dict, frame_idx: int, frame: Path | None, opts: dict) -> dict:
    style = opts.get("cover_style") or DEFAULT_STYLE
    return {
        "text": auto_highlight(clip.get("title") or "", clip.get("keywords")),
        "style": style if style in STYLES else DEFAULT_STYLE,
        "upper": True, "size": 1.0, "tilt": 0, "zoom": 1.0, "px": 0.5, "py": 0.5, "dark": 0.35,
        "y": default_y(frame, clip.get("layout")) if frame else 0.42,
        "handle": opts.get("cover_handle") or "",
        "color": None, "hl_color": None, "frame": frame_idx,
    }


def clean_params(raw: dict, current: dict) -> dict:
    """Valida o que veio do editor por cima dos parâmetros atuais."""
    p = dict(current)
    for k in PARAM_KEYS:
        if k not in raw:
            continue
        v = raw[k]
        if k in ("text", "handle"):
            p[k] = str(v or "")[:200 if k == "text" else 40]
        elif k == "style":
            p[k] = v if v in STYLES else DEFAULT_STYLE
        elif k == "upper":
            p[k] = bool(v)
        elif k in ("color", "hl_color"):
            p[k] = v if isinstance(v, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", v) else None
        elif k == "frame":
            p[k] = max(0, int(v))
        else:
            lo, hi = {"size": (0.4, 1.8), "y": (0, 1), "tilt": (-15, 15), "zoom": (1, 3),
                      "px": (0, 1), "py": (0, 1), "dark": (0, 1)}[k]
            p[k] = min(max(float(v), lo), hi)
    return p


# ------------------------------------------------------------------ capa no vídeo
def attach_to_video(clip_dir: Path, encode, has_audio: bool, fps: float, seconds: float = IN_VIDEO_SECONDS) -> None:
    """Põe a capa como último quadro do clipe (para escolher no "Editar capa" do TikTok, onde
    não dá para subir imagem). Guarda o clipe sem capa em sem_capa.mp4 para refazer depois."""
    plain = clip_dir / "sem_capa.mp4"
    if not plain.exists():
        os.replace(clip_dir / "clip.mp4", plain)
    fps = min(fps or 30, 60)
    graph = (f"[0:v]fps={fps:.3f},format=yuv420p,setsar=1[v0];"
             f"[1:v]scale={W}:{H},fps={fps:.3f},format=yuv420p,setsar=1[v1];")
    if has_audio:
        graph += ("[0:a]aformat=sample_rates=48000:channel_layouts=stereo[a0];"
                  "[2:a]aformat=sample_rates=48000:channel_layouts=stereo[a1];"
                  "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
    else:
        graph += "[v0][v1]concat=n=2:v=1:a=0[v]"
    before = ["ffmpeg", "-y", "-v", "error", "-i", "sem_capa.mp4", "-loop", "1", "-framerate", f"{fps:.3f}",
              "-t", f"{seconds:.2f}", "-i", "capa/capa.jpg"]
    if has_audio:
        before += ["-f", "lavfi", "-t", f"{seconds:.2f}", "-i", "anullsrc=r=48000:cl=stereo"]
    before += ["-filter_complex", graph, "-map", "[v]"]
    after = (["-map", "[a]", "-c:a", "aac", "-b:a", "192k"] if has_audio else []) + \
        ["-pix_fmt", "yuv420p", "-movflags", "+faststart", "clip.mp4"]
    encode(before, after, clip_dir)


def detach_from_video(clip_dir: Path) -> None:
    plain = clip_dir / "sem_capa.mp4"
    if plain.exists():
        os.replace(plain, clip_dir / "clip.mp4")
