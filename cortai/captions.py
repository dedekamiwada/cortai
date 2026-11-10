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

TITLE_POSITIONS = {"top": 150, "upper": 600}  # margem do topo: no alto ou abaixo da webcam
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
Style: Title,Arial Black,58,&H00FFFFFF,&H000000FF,&HC8000000,&HC8000000,-1,0,0,0,100,100,0,0,3,18,0,8,90,90,{title_margin},1

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


def apply_fixes(words: list[dict], fixes: dict[str, str] | None) -> list[dict]:
    """Troca o texto de palavras corrigidas no editor (chave = início da palavra com 2 casas)."""
    if not fixes:
        return words
    out = []
    for w in words:
        key = f"{w['s']:.2f}"
        if key in fixes:
            text = str(fixes[key]).strip()
            if not text:
                continue  # palavra apagada
            w = {**w, "w": text}
        out.append(w)
    return out


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _keyword_set(keywords: list[str] | None) -> set[str]:
    out: set[str] = set()
    for k in keywords or []:
        out.update(_norm(part) for part in str(k).split() if len(_norm(part)) >= 3)
    return out


def _chunks(words: list[dict], max_words: int, max_chars: int) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            text_len = sum(len(x["w"]) + 1 for x in cur) + len(w["w"])
            gap = w["s"] - cur[-1]["e"]
            ends_sentence = cur[-1]["w"][-1:] in ".?!…"
            if len(cur) >= max_words or text_len > max_chars or gap > MAX_GAP or ends_sentence:
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
    title_margin = TITLE_POSITIONS.get(opts.get("title_position") or "top", TITLE_POSITIONS["top"])
    lines = [HEADER.format(font=preset["font"], size=preset["size"], outline=preset["outline"],
                           shadow=preset["shadow"], align=align, margin=margin, title_margin=title_margin)]
    dur = clip_end - clip_start

    if opts.get("title_overlay") and (opts.get("title_text") or "").strip():
        title = _escape(opts["title_text"].strip().upper())
        if opts.get("title_mode") == "hook":
            lines.append(f"Dialogue: 1,{_ts(0)},{_ts(min(dur, HOOK_SECONDS))},Title,,0,0,0,,"
                         f"{{\\fad(120,250)\\t(0,180,\\fscx106\\fscy106)\\t(180,320,\\fscx100\\fscy100)}}{title}\n")
        else:
            lines.append(f"Dialogue: 1,{_ts(0)},{_ts(dur)},Title,,0,0,0,,{{\\fad(150,0)}}{title}\n")

    if not opts.get("captions", True):
        return "".join(lines)

    inside = [w for w in apply_fixes(words, opts.get("word_fixes")) if w["e"] > clip_start and w["s"] < clip_end]
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
    inside = [w for w in apply_fixes(words, opts.get("word_fixes")) if w["e"] > clip_start and w["s"] < clip_end]

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
