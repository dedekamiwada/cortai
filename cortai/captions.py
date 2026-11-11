"""Legendas animadas palavra por palavra em ASS (queimadas pelo libass), com presets,
posição, correção de palavras e título fixo no topo."""
from __future__ import annotations

PRESETS = {
    "destaque": {"label": "Destaque (palavra colorida)", "font": "Arial Black", "size": 86, "outline": 7,
                 "shadow": 3, "max_words": 3, "max_chars": 18, "mode": "color", "pop": 112},
    "marcador": {"label": "Marca-texto (fundo na palavra)", "font": "Arial Black", "size": 84, "outline": 6,
                 "shadow": 0, "max_words": 3, "max_chars": 18, "mode": "box", "pop": 106},
    "uma": {"label": "Uma palavra por vez", "font": "Arial Black", "size": 120, "outline": 9,
            "shadow": 4, "max_words": 1, "max_chars": 99, "mode": "color", "pop": 118},
    "minimal": {"label": "Minimalista", "font": "Arial", "size": 62, "outline": 4, "shadow": 2,
                "max_words": 6, "max_chars": 34, "mode": "none", "pop": 100},
}

POSITIONS = {  # alinhamento ASS (numpad) e margem vertical em 1080x1920
    "bottom": (2, 520),
    "middle": (5, 0),
    "top": (8, 380),
}

# Centro vertical do título (1080x1920). "band" cobre a faixa de título que lives verticais
# (TikTok LIVE Studio) desenham entre a webcam e o jogo.
TITLE_POSITIONS = {"top": 230, "upper": 690, "band": 876, "bottom": 1480}
# Estilos de título de canal de cortes, todos sobre uma faixa de lado a lado (cores ASS em BGR).
# chars = quantos caracteres cabem por linha no tamanho cheio (depende da largura da fonte).
TITLE_STYLES = {
    "impacto": {"label": "Impact + amarelo", "font": "Impact", "size": 108, "chars": 18, "text": "&HFFFFFF&",
                "outline": 8, "highlight": "&H00D4FF&", "bar": "&H100E0E&", "glow": None},
    "faixa": {"label": "Faixa amarela", "font": "Segoe UI Black", "size": 80, "chars": 22, "text": "&H101010&",
              "outline": 0, "highlight": None, "bar": "&H00D4FF&", "glow": None},
    "neon": {"label": "Contorno lima", "font": "Arial Black", "size": 86, "chars": 18, "text": "&HFFFFFF&",
             "outline": 9, "highlight": "&H27F2B9&", "bar": "&H161010&", "glow": "&H27F2B9&"},
}
TITLE_MIN_SCALE = 0.7  # títulos longos diminuem até 70% antes de passar de 3 linhas
HOOK_SECONDS = 3.5  # modo "gancho": título só no começo
MAX_GAP = 0.5

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},&H00FFFFFF,&H000000FF,&H00000000,&H78000000,-1,0,0,0,100,100,1,0,1,{outline},{shadow},{align},70,70,{margin},1
Style: Title,{title_font},{title_size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,1,0,1,{title_outline},0,5,40,40,0,1
Style: TitleGlow,{title_font},{title_size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,1,0,1,{title_glow_w},0,5,40,40,0,1
Style: TitleBar,Arial,10,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_color(hex_color: str) -> str:
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        h = "FFE600"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}&".upper()


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def expand_adds(adds: list[dict] | None) -> list[dict]:
    """Legendas adicionadas à mão no editor ({w, s, e} em segundos do vídeo original) viram
    palavras: um texto com várias palavras é dividido igualmente no intervalo. `added` guarda
    o número da legenda, para ela não se misturar com a fala vizinha no mesmo bloco."""
    out: list[dict] = []
    for g, a in enumerate(adds or [], 1):
        try:
            s, e = float(a["s"]), float(a["e"])
            parts = str(a.get("w") or "").split()
        except (KeyError, TypeError, ValueError):
            continue
        if not parts or e <= s:
            continue
        step = (e - s) / len(parts)
        out += [{"w": p, "s": round(s + i * step, 3), "e": round(s + (i + 1) * step, 3), "added": g}
                for i, p in enumerate(parts)]
    return out


def with_adds(words: list[dict], adds: list[dict] | None) -> list[dict]:
    extra = expand_adds(adds)
    return sorted(words + extra, key=lambda w: w["s"]) if extra else words


def apply_fixes(words: list[dict], fixes: dict[str, str] | None, adds: list[dict] | None = None) -> list[dict]:
    """Troca o texto de palavras corrigidas no editor (chave = início da palavra com 2 casas)
    e acrescenta as legendas adicionadas à mão (que escondem a fala no mesmo intervalo)."""
    out = words
    if fixes:
        out = []
        for w in words:
            key = f"{w['s']:.2f}"
            if key in fixes:
                text = str(fixes[key]).strip()
                if not text:
                    continue  # palavra apagada
                w = {**w, "w": text}
            out.append(w)
    extra = expand_adds(adds)
    if not extra:
        return out
    # a legenda adicionada toma o lugar da fala no trecho dela (senão as duas se alternam na tela)
    spans = [(a["s"], a["e"]) for a in adds if isinstance(a, dict) and a.get("w")]
    out = [w for w in out if not any(_covers(sp, w) for sp in spans)]
    return sorted(out + extra, key=lambda w: w["s"])


def _covers(span, w: dict) -> bool:
    try:
        s, e = float(span[0]), float(span[1])
    except (TypeError, ValueError):
        return False
    return s - 0.05 <= w["s"] < e


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _keyword_set(keywords: list[str] | None) -> set[str]:
    out: set[str] = set()
    for k in keywords or []:
        out.update(_norm(part) for part in str(k).split() if len(_norm(part)) >= 3)
    return out


def _title_lines(title: str, style: dict) -> tuple[list[str], int]:
    """Quebra o título em até 3 linhas e escolhe o tamanho da fonte para caber na largura."""
    words = title.split()
    full = style["size"]
    for size in range(full, int(full * TITLE_MIN_SCALE) - 1, -6):
        limit = int(style["chars"] * full / size)
        lines, cur = [], ""
        for w in words:
            if cur and len(cur) + 1 + len(w) > limit:
                lines.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        lines.append(cur)
        if len(lines) <= 3:
            return lines, size
    return lines, size


def _title_events(text: str, keywords: list[str] | None, dur: float, mode: str, position, style: dict) -> list[str]:
    """Título sobre uma faixa de lado a lado, que também esconde o que houver embaixo (como o título
    que a própria live desenha). Palavra-chave do clipe em destaque; sem ela, a palavra mais longa."""
    title = _escape(text.strip().upper())
    lines, size = _title_lines(title, style)
    words = [w for line in lines for w in line.split()]
    marked: set[str] = set()
    if style["highlight"] and words:
        keys = _keyword_set(keywords)
        marked = {w for w in words if _norm(w) in keys} or {max(words, key=lambda w: len(_norm(w)))}

    def paint(line: str) -> str:
        return " ".join(f"{{\\c{style['highlight']}}}{w}{{\\c{style['text']}}}" if w in marked else w
                        for w in line.split())

    y = TITLE_POSITIONS.get(position, TITLE_POSITIONS["top"])
    line_h = round(size * 1.05)
    bar_h = line_h * len(lines) + 56
    top = max(0, min(1920 - bar_h, y - bar_h // 2))
    center = top + bar_h // 2
    end = min(dur, HOOK_SECONDS) if mode == "hook" else dur
    fx = "\\fad(120,250)" if mode == "hook" else "\\fad(150,0)"
    pop = "\\t(0,180,\\fscx106\\fscy106)\\t(180,320,\\fscx100\\fscy100)" if mode == "hook" else ""
    head = f"{{\\pos(540,{center}){fx}{pop}\\fs{size}\\c{style['text']}"
    span = f"{_ts(0)},{_ts(end)}"
    events = [f"Dialogue: 1,{span},TitleBar,,0,0,0,,{{\\pos(0,{top})\\1c{style['bar']}{fx}\\p1}}"
              f"m 0 0 l 1080 0 1080 {bar_h} 0 {bar_h}{{\\p0}}\n"]
    if style["glow"]:  # contorno colorido borrado por trás do contorno preto
        events.append(f"Dialogue: 2,{span},TitleGlow,,0,0,0,,{head}\\1c{style['glow']}\\3c{style['glow']}\\blur3}}"
                      + "\\N".join(lines) + "\n")
    events.append(f"Dialogue: 3,{span},Title,,0,0,0,,{head}}}" + "\\N".join(paint(line) for line in lines) + "\n")
    return events


def _chunks(words: list[dict], max_words: int, max_chars: int) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            text_len = sum(len(x["w"]) + 1 for x in cur) + len(w["w"])
            gap = w["s"] - cur[-1]["e"]
            ends_sentence = cur[-1]["w"][-1:] in ".?!…"
            other_source = cur[-1].get("added") != w.get("added")  # legenda manual fica no próprio bloco
            if len(cur) >= max_words or text_len > max_chars or gap > MAX_GAP or ends_sentence or other_source:
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[dict], clip_start: float, clip_end: float, opts: dict) -> str:
    """`words` com tempos absolutos do vídeo original; a saída usa tempos relativos ao clipe."""
    preset = PRESETS.get(opts.get("caption_preset") or "destaque", PRESETS["destaque"])
    align, margin = POSITIONS.get(opts.get("caption_position") or "bottom", POSITIONS["bottom"])
    hi = _ass_color(opts.get("caption_color", "#FFE600"))
    upper = bool(opts.get("caption_upper", True))
    tstyle = TITLE_STYLES.get(opts.get("title_style") or "impacto", TITLE_STYLES["impacto"])
    lines = [HEADER.format(font=preset["font"], size=preset["size"], outline=preset["outline"],
                           shadow=preset["shadow"], align=align, margin=margin, title_font=tstyle["font"],
                           title_size=tstyle["size"], title_outline=tstyle["outline"],
                           title_glow_w=tstyle["outline"] * 2)]
    dur = clip_end - clip_start

    if opts.get("title_overlay") and (opts.get("title_text") or "").strip():
        lines += _title_events(opts["title_text"], opts.get("keywords"), dur, opts.get("title_mode") or "always",
                               opts.get("title_position") or "top", tstyle)

    if not opts.get("captions", True):
        return "".join(lines)

    inside = [w for w in apply_fixes(words, opts.get("word_fixes"), opts.get("word_adds")) if w["e"] > clip_start and w["s"] < clip_end]
    chunks = _chunks(inside, preset["max_words"], preset["max_chars"])
    pop = preset["pop"]
    keys = _keyword_set(opts.get("keywords")) if opts.get("keyword_highlight", True) else set()
    kw = _ass_color(opts.get("keyword_color") or "#39E75F")
    for ci, chunk in enumerate(chunks):
        next_start = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else clip_end
        labels = [_escape(w["w"].upper() if upper else w["w"]) for w in chunk]
        is_key = [_norm(w["w"]) in keys for w in chunk]
        for i, w in enumerate(chunk):
            start = w["s"]
            end = chunk[i + 1]["s"] if i + 1 < len(chunk) else min(next_start, w["e"] + 0.35)
            if end <= start:
                end = start + 0.08
            parts = []
            for j, label in enumerate(labels):
                if j != i or preset["mode"] == "none":
                    parts.append(f"{{\\c{kw}}}{label}{{\\r}}" if is_key[j] else label)
                elif preset["mode"] == "box":
                    # contorno grosso colorido + texto escuro = efeito marca-texto
                    parts.append(f"{{\\3c{hi}\\c&H00111111&\\bord16\\fscx{pop}\\fscy{pop}}}{label}{{\\r}}")
                else:
                    parts.append(f"{{\\c{hi}\\fscx{pop}\\fscy{pop}}}{label}{{\\r}}")
            text = " ".join(parts)
            if i == 0:
                text = "{\\fad(60,0)}" + text
            lines.append(f"Dialogue: 0,{_ts(start - clip_start)},{_ts(end - clip_start)},Default,,0,0,0,,{text}\n")
    return "".join(lines)


def build_srt(words: list[dict], clip_start: float, clip_end: float, opts: dict) -> str:
    """Legenda .srt (para subir como legenda nativa no TikTok/YouTube), em frases curtas."""
    inside = [w for w in apply_fixes(words, opts.get("word_fixes"), opts.get("word_adds")) if w["e"] > clip_start and w["s"] < clip_end]

    def stamp(t: float) -> str:
        ms = int(round(max(0.0, t) * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    out = []
    for i, chunk in enumerate(_chunks(inside, 7, 42), 1):
        text = " ".join(w["w"] for w in chunk)
        out.append(f"{i}\n{stamp(chunk[0]['s'] - clip_start)} --> {stamp(chunk[-1]['e'] - clip_start)}\n{text}\n")
    return "\n".join(out)
