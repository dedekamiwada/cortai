"""Exportação do Estúdio: transforma o projeto num único filter_complex do ffmpeg."""
from __future__ import annotations

import subprocess
import time
import traceback
from pathlib import Path

from . import fx, studio

XFADE = {  # nome na interface -> transição do ffmpeg xfade
    "fade": "fade", "dissolve": "dissolve", "fadeblack": "fadeblack", "fadewhite": "fadewhite",
    "wipeleft": "wipeleft", "wiperight": "wiperight", "wipeup": "wipeup", "wipedown": "wipedown",
    "slideleft": "slideleft", "slideright": "slideright", "slideup": "slideup", "slidedown": "slidedown",
    "smoothleft": "smoothleft", "smoothright": "smoothright", "circleopen": "circleopen",
    "circleclose": "circleclose", "radial": "radial", "pixelize": "pixelize", "zoomin": "zoomin",
    "hblur": "hblur", "squeezeh": "squeezeh", "distance": "distance",
}
AUDIO_FMT = "aformat=sample_rates=48000:channel_layouts=stereo"


def _even(v: float) -> int:
    return max(2, int(round(v)) // 2 * 2)


def _atempo(speed: float) -> str:
    """atempo aceita 0,5–2,0 por estágio; encadeia para velocidades maiores/menores."""
    parts, s = [], max(0.1, min(16.0, speed))
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    if abs(s - 1.0) > 1e-3:
        parts.append(f"atempo={s:.4f}")
    return ",".join(parts)


def _ass_color(hex_color: str) -> str:
    h = (hex_color or "#FFFFFF").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _ts(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _tokens(text: str) -> list[list[str]]:
    """Palavras por linha (o destaque conta as palavras em sequência, atravessando as linhas)."""
    return [line.split() for line in text.split("\n")]


def word_spans(t: dict, count: int) -> list[tuple[float, float]]:
    """Tempo de cada palavra (relativo ao início do texto). Se o texto foi editado e o número de
    palavras mudou, divide a duração igualmente."""
    dur = max(0.1, float(t.get("dur", 3)))
    spans = t.get("words") or []
    if len(spans) == count and count:
        out = []
        for a, b in spans:
            a, b = max(0.0, float(a)), min(dur, float(b))
            out.append((a, max(b, a + 0.04)))
        return out
    step = dur / max(1, count)
    return [(i * step, (i + 1) * step) for i in range(count)]


def _rich(t: dict, lines: list[list[str]], active: int | None, base: str, base_color: str) -> str:
    """Texto com a palavra `active` em destaque e as palavras-chave coloridas. `base` são as tags
    que devolvem o estilo do texto depois de cada palavra marcada."""
    kw = {int(i) for i in t.get("kw_idx") or []}
    kwc = _ass_color(t.get("kw_color") or "#39E75F")
    hl = _ass_color(t.get("hl") or "#FFE600")
    pop = int(t.get("hl_pop") or 110)
    mode = t.get("hl_mode") or "color"
    out_lines, k = [], 0
    for line in lines:
        parts = []
        for word in line:
            if k == active and mode == "box":
                parts.append(f"{{\\3c{hl}\\c&H00111111&\\bord16\\fscx{pop}\\fscy{pop}}}{word}{{{base}}}")
            elif k == active and mode == "color":
                parts.append(f"{{\\c{hl}\\fscx{pop}\\fscy{pop}}}{word}{{{base}}}")
            elif k in kw:
                parts.append(f"{{\\c{kwc}}}{word}{{\\c{base_color}}}")
            else:
                parts.append(word)
            k += 1
        out_lines.append(" ".join(parts))
    return "\\N".join(out_lines)


def text_ass(p: dict) -> str:
    W, H = p["canvas"]["w"], p["canvas"]["h"]
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        "Style: Out,Arial,80,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,4,2,5,40,40,40,1",
        "Style: Box,Arial,80,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,3,14,0,5,40,40,40,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for i, t in enumerate(p["tracks"]["text"]):
        text = str(t.get("text") or "").replace("\\", "").replace("{", "(").replace("}", ")")
        if not text.strip():
            continue
        x, y = float(t.get("x", 0.5)) * W, float(t.get("y", 0.5)) * H
        start, dur = float(t.get("start", 0)), max(0.1, float(t.get("dur", 3)))
        box = bool(t.get("bg"))
        color = _ass_color(t.get("color") or "#FFFFFF")
        tags = [f"\\an5\\pos({x:.0f},{y:.0f})", f"\\fn{t.get('font') or 'Arial Black'}", f"\\fs{int(t.get('size') or 80)}",
                f"\\c{color}", f"\\b{1 if t.get('bold', True) else 0}", f"\\i{1 if t.get('italic') else 0}"]
        if box:
            outline = f"\\3c{_ass_color(t['bg'])}\\bord14"
        else:
            outline = f"\\3c{_ass_color(t.get('stroke') or '#000000')}\\bord{float(t.get('stroke_w', 5)):.1f}"
        tags.append(outline)
        if t.get("shadow"):  # sombra projetada: distância em px do quadro e opacidade 0–1
            d = max(0.0, min(40.0, float(t.get("shadow_d", 6) or 0)))
            alpha = round((1 - max(0.0, min(1.0, float(t.get("shadow_a", 0.8))))) * 255)
            tags.append(f"\\shad{d:.1f}\\4c{_ass_color(t['shadow'])}\\4a&H{alpha:02X}&")
        base = f"\\c{color}{outline}\\fscx100\\fscy100"
        anim = t.get("anim") or "none"
        fade_in, fade_out, intro = 0, 0, ""
        if anim == "fade":
            fade_in, fade_out = 250, 250
        elif anim == "pop":
            fade_out = 180
            intro = "\\fscx30\\fscy30\\t(0,160,\\fscx112\\fscy112)\\t(160,260,\\fscx100\\fscy100)"
        elif anim == "slide":
            fade_in, fade_out = 160, 160
            intro = f"\\move({x:.0f},{y + H * 0.06:.0f},{x:.0f},{y:.0f},0,260)"
        elif anim == "zoom":
            fade_in, fade_out = 120, 180
            intro = "\\fscx160\\fscy160\\t(0,300,\\fscx100\\fscy100)"
        style = "Box" if box else "Out"
        words = _tokens(text)
        count = sum(len(line) for line in words)
        karaoke = bool(t.get("words")) and (t.get("hl_mode") or "color") in ("color", "box") and count > 0
        if not karaoke:
            body = _rich(t, words, None, base, color) if t.get("kw_idx") else text.replace("\n", "\\N")
            fad = f"\\fad({fade_in},{fade_out})" if fade_in or fade_out else ""
            lines.append(f"Dialogue: {i},{_ts(start)},{_ts(start + dur)},{style},,0,0,0,,"
                         f"{{{''.join(tags)}{intro}{fad}}}{body}")
            continue
        # destaque da palavra falada: um evento por palavra, como as legendas do Cortaí
        spans = word_spans(t, count)
        for k, (a, b) in enumerate(spans):
            if k == 0:
                a = 0.0  # o texto aparece inteiro desde o começo
            if k + 1 < len(spans):
                b = max(b, spans[k + 1][0])  # sem buraco entre uma palavra e a próxima
            else:
                b = dur
            if b - a < 0.02:
                continue
            first, last = k == 0, k == len(spans) - 1
            fi, fo = (fade_in if first else 0), (fade_out if last else 0)
            fad = f"\\fad({fi},{fo})" if fi or fo else ""
            lines.append(f"Dialogue: {i},{_ts(start + a)},{_ts(start + b)},{style},,0,0,0,,"
                         f"{{{''.join(tags)}{intro if first else ''}{fad}}}{_rich(t, words, k, base, color)}")
    return "\n".join(lines) + "\n"


def _crop(it: dict) -> str | None:
    """Cortar bordas (frações de cada lado), antes de encaixar no quadro."""
    l, r, t, b = (max(0.0, min(0.45, float(it.get(k) or 0))) for k in ("crop_l", "crop_r", "crop_t", "crop_b"))
    if l + r + t + b < 1e-3:
        return None
    w, h = 1 - l - r, 1 - t - b
    return f"crop=trunc(iw*{w:.4f}/2)*2:trunc(ih*{h:.4f}/2)*2:trunc(iw*{l:.4f}):trunc(ih*{t:.4f})"


def _flips(it: dict) -> list[str]:
    return [f for f, on in (("hflip", it.get("flip_h")), ("vflip", it.get("flip_v"))) if on]


def _angle(it: dict) -> float:
    """Rotação em graus, entre -180 e 180 (0 = sem rotação)."""
    try:
        a = float(it.get("rotate") or 0) % 360
    except (TypeError, ValueError):
        return 0.0
    a = a - 360 if a > 180 else a
    return 0.0 if abs(a) < 0.05 else a


def _fit_chain(label_in: str, label_out: str, it: dict, W: int, H: int, bg: str) -> str:
    fit = it.get("fit") or "fill"
    z = max(1.0, float(it.get("zoom") or 1.0))
    if fit == "fit":
        return (f"[{label_in}]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={bg}[{label_out}]")
    if fit == "blur":
        return (f"[{label_in}]split=2[{label_out}a][{label_out}b];"
                f"[{label_out}a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=28,"
                f"eq=brightness=-0.08[{label_out}bg];"
                f"[{label_out}b]scale={W}:{H}:force_original_aspect_ratio=decrease[{label_out}fg];"
                f"[{label_out}bg][{label_out}fg]overlay=(W-w)/2:(H-h)/2[{label_out}]")
    px = max(-1.0, min(1.0, float(it.get("pan_x") or 0)))
    py = max(-1.0, min(1.0, float(it.get("pan_y") or 0)))
    return (f"[{label_in}]scale={_even(W * z)}:{_even(H * z)}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H}:(iw-{W})/2*(1+{px:.3f}):(ih-{H})/2*(1+{py:.3f})[{label_out}]")


def build(p: dict, out_dir: Path, scale_to: int | None = None) -> tuple[list[str], str, float]:
    """Monta (argumentos de entrada, filter_complex, duração) para o ffmpeg."""
    W, H, fps, bg = p["canvas"]["w"], p["canvas"]["h"], p["canvas"]["fps"], p["canvas"]["bg"]
    bgc = "0x" + bg.lstrip("#")
    assets = p["assets"]
    layout = studio.main_layout(p)
    if not layout:
        raise ValueError("a faixa principal está vazia — arraste um vídeo ou imagem para a timeline")
    total = layout[-1][2]
    inputs: list[str] = []
    graph: list[str] = []
    k = 0

    def add_still(path: Path) -> int:  # imagem parada gerada na exportação (ex.: máscara da vinheta)
        nonlocal k
        inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{total + 1:.3f}", "-i", path.name])
        k += 1
        return k - 1

    def add_input(asset: dict, start: float, length: float) -> int:
        nonlocal k
        if asset["kind"] == "image":
            inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{length:.3f}", "-i", asset["path"]])
        else:
            inputs.extend(["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", asset["path"]])
        k += 1
        return k - 1

    # ---- faixa principal
    cur_v = cur_a = None
    cur_len = 0.0
    for i, (it, start, end) in enumerate(layout):
        a = assets[it["asset"]]
        speed = 1.0 if a["kind"] == "image" else max(0.1, float(it.get("speed") or 1))
        src_len = float(it["out"]) - float(it["in"])
        length = end - start
        idx = add_input(a, float(it["in"]), src_len)
        crop = _crop(it)
        chain = f"[{idx}:v]setpts=(PTS-STARTPTS)/{speed:.4f},fps={fps},format=yuv420p{',' + crop if crop else ''}[r{i}];"
        chain += _fit_chain(f"r{i}", f"f{i}", it, W, H, bgc) + ";"
        b, c, s = float(it.get("brightness") or 0), float(it.get("contrast") or 1), float(it.get("saturation") or 1)
        eq = f"eq=brightness={b:.3f}:contrast={c:.3f}:saturation={s:.3f}" \
            if abs(b) > 1e-3 or abs(c - 1) > 1e-3 or abs(s - 1) > 1e-3 else "null"
        chain += f"[f{i}]{eq}[e{i}];"
        # filtro de cor e efeitos (tremor, vinheta...) sobre o quadro encaixado
        chain += fx.main_fx(f"e{i}", f"g{i}", it, W, H, f"m{i}", out_dir, add_still) + ";"
        # espelhar e girar valem sobre o quadro já encaixado (igual à prévia)
        post = _flips(it)
        ang = _angle(it)
        if ang:
            post.append(f"rotate={ang:.3f}*PI/180:ow={W}:oh={H}:c={bgc}")
        fi, fo = float(it.get("fade_in") or 0), float(it.get("fade_out") or 0)
        if fi > 0:
            post.append(f"fade=t=in:st=0:d={fi:.2f}")
        if fo > 0:
            post.append(f"fade=t=out:st={max(0, length - fo):.2f}:d={fo:.2f}")
        post += ["setsar=1", "format=yuv420p", "settb=AVTB"]
        chain += f"[g{i}]{','.join(post)}[v{i}]"
        graph.append(chain)

        vol = 0.0 if it.get("mute") else float(it.get("volume", 1.0))
        if a.get("has_audio") and vol > 0:
            afx = ["asetpts=PTS-STARTPTS", _atempo(speed), f"volume={vol:.3f}", *fx.voice_chain(it)]
            if fi > 0:
                afx.append(f"afade=t=in:st=0:d={fi:.2f}")
            if fo > 0:
                afx.append(f"afade=t=out:st={max(0, length - fo):.2f}:d={fo:.2f}")
            afx += [AUDIO_FMT, f"apad=whole_dur={length:.3f}", f"atrim=duration={length:.3f}"]
            graph.append(f"[{idx}:a]{','.join(x for x in afx if x)}[a{i}]")
        else:
            graph.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={length:.3f}[a{i}]")

        if i == 0:
            cur_v, cur_a, cur_len = f"v{i}", f"a{i}", length
            continue
        overlap = layout[i - 1][2] - start
        trans = (it.get("transition") or {}).get("type", "none")
        if overlap > 0.01 and trans in fx.CUT_TRANSITIONS:
            # troca no meio da janela [off, off+d]: o anterior vai até off+d/2 (com o efeito na última
            # metade) e este entra a partir do seu d/2 (com o efeito até d), como na prévia
            h, off = overlap / 2, cur_len - overlap
            graph.append(f"[{cur_v}]split=2[ca{i}][cb{i}]")
            graph.append(f"[ca{i}]trim=0:{off:.4f},setpts=PTS-STARTPTS[pa{i}]")
            graph.append(f"[cb{i}]trim={off:.4f}:{off + h:.4f},setpts=PTS-STARTPTS,{fx.cut_chain(trans, 'out', h, W, H)}[pb{i}]")
            graph.append(f"[v{i}]split=2[na{i}][nb{i}]")
            graph.append(f"[na{i}]trim={h:.4f}:{overlap:.4f},setpts=PTS-STARTPTS,{fx.cut_chain(trans, 'in', h, W, H)}[pc{i}]")
            graph.append(f"[nb{i}]trim={overlap:.4f},setpts=PTS-STARTPTS[pd{i}]")
            graph.append(f"[pa{i}][pb{i}][pc{i}][pd{i}]concat=n=4:v=1:a=0,settb=AVTB[vx{i}]")
            graph.append(f"[{cur_a}][a{i}]acrossfade=d={overlap:.3f}[ax{i}]")
            cur_len += length - overlap
        elif overlap > 0.01 and trans == "lightleak":
            off = cur_len - overlap
            graph.append(f"[{cur_v}][v{i}]xfade=transition=fade:duration={overlap:.3f}:offset={off:.3f}[vf{i}]")
            leak = out_dir / "luz.png"
            if not leak.exists():
                fx.leak_png(leak, W, H)
            li = add_still(leak)
            graph.append(f"[{li}:v]format=rgba,fade=t=in:st={off:.3f}:d={overlap / 2:.3f}:alpha=1,"
                         f"fade=t=out:st={off + overlap / 2:.3f}:d={overlap / 2:.3f}:alpha=1[lk{i}]")
            graph.append(f"[vf{i}][lk{i}]overlay=x='-w+(W+w)*(t-{off:.3f})/{overlap:.3f}':y=0:"
                         f"enable='between(t,{off:.3f},{off + overlap:.3f})'[vx{i}]")
            graph.append(f"[{cur_a}][a{i}]acrossfade=d={overlap:.3f}[ax{i}]")
            cur_len += length - overlap
        elif overlap > 0.01 and trans in XFADE:
            graph.append(f"[{cur_v}][v{i}]xfade=transition={XFADE[trans]}:duration={overlap:.3f}:"
                         f"offset={cur_len - overlap:.3f}[vx{i}]")
            graph.append(f"[{cur_a}][a{i}]acrossfade=d={overlap:.3f}[ax{i}]")
            cur_len += length - overlap
        else:
            graph.append(f"[{cur_v}][{cur_a}][v{i}][a{i}]concat=n=2:v=1:a=1[vx{i}][ax{i}]")
            cur_len += length
        cur_v, cur_a = f"vx{i}", f"ax{i}"

    # ---- sobreposições (picture-in-picture)
    mixes: list[str] = []
    for j, it in enumerate(p["tracks"]["overlay"]):
        a = assets[it["asset"]]
        start = float(it.get("start", 0))
        length = max(0.1, float(it["out"]) - float(it["in"]))
        if start >= total:
            continue
        idx = add_input(a, float(it["in"]), length)
        ow = _even(W * max(0.05, min(2.0, float(it.get("scale") or 0.4))))
        op = max(0.0, min(1.0, float(it.get("opacity", 1.0))))
        crop = _crop(it)
        ofx = [f"setpts=PTS-STARTPTS+{start:.3f}/TB", *([crop] if crop else []), f"scale={ow}:-2", "format=rgba"]
        b, c, s = float(it.get("brightness") or 0), float(it.get("contrast") or 1), float(it.get("saturation") or 1)
        if abs(b) > 1e-3 or abs(c - 1) > 1e-3 or abs(s - 1) > 1e-3:
            ofx.insert(len(ofx) - 1, f"eq=brightness={b:.3f}:contrast={c:.3f}:saturation={s:.3f}")
        key = fx.key_filter(it)
        if key:  # recorte de fundo antes de mexer na cor, para comparar com a cor original
            ofx.insert(len(ofx) - 1, key)
        m = fx.look_matrix(it)
        if m:
            fx.write_cube(m, out_dir / f"look-o{j}.cube")
            ofx.insert(len(ofx) - 1, f"lut3d=file=look-o{j}.cube:interp=trilinear")
        ofx += _flips(it)
        ang = _angle(it)
        if ang:  # o quadro cresce para caber a imagem girada; o centro continua no mesmo ponto
            ofx.append(f"rotate={ang:.3f}*PI/180:ow=rotw({ang:.3f}*PI/180):oh=roth({ang:.3f}*PI/180):c=none")
        if op < 0.999:
            ofx.append(f"colorchannelmixer=aa={op:.3f}")
        graph.append(f"[{idx}:v]{','.join(ofx)}[o{j}]")
        x, y = float(it.get("x", 0.5)) * W, float(it.get("y", 0.5)) * H
        shake = fx.effect(it, "shake")
        xs = f"+{W * 0.03:.1f}*{fx.shake_expr('x', shake)}" if shake else ""
        ys = f"+{W * 0.03:.1f}*{fx.shake_expr('y', shake)}" if shake else ""
        place = (f"x='{x:.0f}-w/2{xs}':y='{y:.0f}-h/2{ys}':eof_action=pass:"
                 f"enable='between(t,{start:.3f},{start + length:.3f})'")
        bm = fx.blend_mode(it)
        if bm:
            # mesclagem: a sobreposição vai para um quadro da cor neutra do modo (que não altera o
            # vídeo de baixo) e o quadro inteiro é misturado por cima, em RGB
            mode, neutral = bm
            graph.append(f"color=c={neutral}:s={W}x{H}:r={fps}:d={total + 1:.3f},format=rgba[nb{j}]")
            graph.append(f"[nb{j}][o{j}]overlay={place}:format=auto,format=gbrp[ol{j}]")
            graph.append(f"[{cur_v}]format=gbrp[bb{j}]")
            graph.append(f"[bb{j}][ol{j}]blend=all_mode={mode}:shortest=1,format=yuv420p,setsar=1[ov{j}]")
        else:
            graph.append(f"[{cur_v}][o{j}]overlay={place}[ov{j}]")
        cur_v = f"ov{j}"
        vol = 0.0 if it.get("mute") else float(it.get("volume", 0.0))
        if a.get("has_audio") and vol > 0:
            ms = int(start * 1000)
            vfx = "".join(f"{f}," for f in fx.voice_chain(it))
            graph.append(f"[{idx}:a]asetpts=PTS-STARTPTS,volume={vol:.3f},{vfx}{AUDIO_FMT},adelay={ms}|{ms}[om{j}]")
            mixes.append(f"om{j}")

    # ---- faixa de áudio (música, efeitos, narração)
    for j, it in enumerate(p["tracks"]["audio"]):
        a = assets[it["asset"]]
        if not a.get("has_audio") or it.get("mute"):
            continue
        start = float(it.get("start", 0))
        if start >= total:
            continue
        speed = max(0.1, float(it.get("speed") or 1))
        src_len = max(0.1, float(it["out"]) - float(it["in"]))
        length = src_len / speed
        idx = add_input(a, float(it["in"]), src_len)
        afx = ["asetpts=PTS-STARTPTS", _atempo(speed), f"volume={float(it.get('volume', 1.0)):.3f}", *fx.voice_chain(it)]
        fi, fo = float(it.get("fade_in") or 0), float(it.get("fade_out") or 0)
        if fi > 0:
            afx.append(f"afade=t=in:st=0:d={fi:.2f}")
        if fo > 0:
            afx.append(f"afade=t=out:st={max(0, length - fo):.2f}:d={fo:.2f}")
        ms = int(start * 1000)
        afx += [AUDIO_FMT, f"adelay={ms}|{ms}"]
        graph.append(f"[{idx}:a]{','.join(x for x in afx if x)}[m{j}]")
        mixes.append(f"m{j}")

    if mixes:
        labels = "".join(f"[{m}]" for m in [cur_a, *mixes])
        graph.append(f"{labels}amix=inputs={len(mixes) + 1}:duration=first:normalize=0[aout]")
    else:
        graph.append(f"[{cur_a}]anull[aout]")

    # ---- textos e escala final
    tail = []
    if any(str(t.get("text") or "").strip() for t in p["tracks"]["text"]):
        (out_dir / "text.ass").write_text(text_ass(p), encoding="utf-8")
        tail.append("subtitles=text.ass")
    if scale_to and scale_to < min(W, H):
        f = scale_to / min(W, H)
        tail.append(f"scale={_even(W * f)}:{_even(H * f)}:flags=lanczos")
    graph.append(f"[{cur_v}]{','.join(tail) if tail else 'null'}[vout]")
    return inputs, ";".join(graph), total


def export(pid: str, opts: dict) -> None:
    """Roda em segundo plano; o progresso fica em project["export"]."""
    def set_export(**fields):
        studio.mutate(pid, lambda p: p.__setitem__("export", {**(p.get("export") or {}), **fields}))

    try:
        p = studio.load(pid)
        out_dir = studio._pdir(pid) / "exports"
        out_dir.mkdir(exist_ok=True)
        name = "".join(ch for ch in p["name"] if ch.isalnum() or ch in " -_")[:60].strip() or "video"
        fname = f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
        scale_to = {"720": 720, "1080": None}.get(str(opts.get("resolution") or "1080"))
        cq = {"alta": 19, "media": 24, "leve": 29}.get(opts.get("quality") or "alta", 19)
        inputs, graph, total = build(p, out_dir, scale_to)
        (out_dir / "last_filter.txt").write_text(graph, encoding="utf-8")  # para depurar
        base = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1", *inputs,
                "-/filter_complex", "last_filter.txt", "-map", "[vout]", "-map", "[aout]",
                "-t", f"{total:.3f}", "-r", str(p["canvas"]["fps"])]
        encoders = [
            ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(cq), "-b:v", "0"],
            ["-c:v", "libx264", "-preset", "medium", "-crf", str(cq)],
        ]
        tail = ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", fname]
        error = ""
        for enc in encoders:
            set_export(status="running", progress=0.0, error=None, file=None, started=time.time())
            proc = subprocess.Popen(base + enc + tail, cwd=out_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", errors="replace")
            last = 0.0
            for line in proc.stdout:
                key, _, val = line.strip().partition("=")
                if key == "out_time_us" and val.isdigit() and total:
                    prog = min(1.0, int(val) / 1e6 / total)
                    if prog - last >= 0.02:
                        last = prog
                        set_export(progress=round(prog, 3))
            error = proc.stderr.read()
            if proc.wait() == 0:
                set_export(status="done", progress=1.0, file=f"_studio/{pid}/exports/{fname}",
                           finished=time.time(), duration=round(total, 2))
                return
        raise RuntimeError(error.strip()[-1500:] or "ffmpeg falhou")
    except Exception as exc:
        traceback.print_exc()
        set_export(status="error", error=str(exc))
