"""Renderização de um clipe vertical 1080x1920 com ffmpeg.

Layouts:
  face   — corte 9:16 seguindo o rosto (vídeo horizontal)
  manual — corte 9:16 fixo na posição escolhida no editor (crop_x 0..1)
  split  — tela dividida: webcam em cima, jogo embaixo (vídeo horizontal com rosto)
  fit    — vídeo inteiro no meio com fundo desfocado
  auto   — face se houver rosto em ≥35% do trecho, senão fit

Efeitos:
  jump cuts  — `keep` (trechos a manter) remove pausas mortas
  zoom       — `zooms` (segundos relativos ao clipe original) dá um "punch-in" na reação
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import capa, captions, reframe
from .edit import TimeMap

OUT_W, OUT_H = 1080, 1920
SPLIT_TOP = 768  # altura do painel da webcam; o jogo fica com 1152
ZOOM = 1.32
ZOOM_HOLD = 1.4
LOUDNESS, TRUE_PEAK = -14, -2.0  # TikTok/Reels/Shorts; pico com folga porque o AAC sobe os picos ~1 dB
# Voz destacada: tira o grave abafado (250 Hz), realça a presença (3,2 kHz) e o ar, comprime e segura o
# pico do grito. Testado num corte de live: a faixa dinâmica caiu de 8 para ~5 LU sem estourar.
VOICE_CHAIN = ("highpass=f=85,equalizer=f=250:t=q:w=1.2:g=-3,equalizer=f=3200:t=q:w=1.0:g=4,"
               "highshelf=f=9000:g=2,acompressor=threshold=-22dB:ratio=4:attack=4:release=150:makeup=6dB:knee=4,"
               "alimiter=limit=0.84:attack=2:release=60:level=disabled")
_encoder: list[str] | None = None


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def _encoders() -> list[list[str]]:
    nvenc = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"]
    x264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    return [_encoder] if _encoder else [nvenc, x264]


def _run_encode(args_before_enc: list[str], args_after_enc: list[str], cwd: Path) -> None:
    global _encoder
    error = ""
    for enc in _encoders():
        proc = subprocess.run(args_before_enc + enc + args_after_enc, cwd=cwd, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        if proc.returncode == 0:
            _encoder = enc
            return
        error = proc.stderr[-1500:]
    raise RuntimeError(f"ffmpeg falhou ao renderizar: {error}")


def encode(args_before_enc: list[str], args_after_enc: list[str], cwd: Path) -> None:
    """Encoda com o mesmo codificador do render (NVENC quando houver, senão x264)."""
    _run_encode(args_before_enc, args_after_enc, cwd)


def _audio_parts(tm: TimeMap, voice: bool = False) -> tuple[str, str]:
    """Grafo de áudio dos trechos mantidos (jump cuts) e da voz destacada. Devolve (filtros, rótulo de saída)."""
    if not tm.trimmed and not voice:
        return "", "0:a:0"
    parts, label = [], "0:a:0"
    if tm.trimmed:
        parts = [f"[0:a:0]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]" for i, (a, b) in enumerate(tm.keep)]
        parts.append("".join(f"[a{i}]" for i in range(len(tm.keep))) + f"concat=n={len(tm.keep)}:v=0:a=1[ain]")
        label = "ain"
    if voice:
        parts.append(f"[{label}]{VOICE_CHAIN}[avoz]")
        label = "avoz"
    return ";".join(parts) + ";", label


def _loudnorm(src: Path, start: float, dur: float, tm: TimeMap, voice: bool = False) -> str | None:
    """1ª passada do loudnorm: mede o áudio exatamente como vai sair (com os cortes e a voz tratada).
    Devolve o filtro da 2ª passada, linear (sem "bombear" o volume), ou None para clipe sem som."""
    pre, label = _audio_parts(tm, voice)
    graph = pre + f"[{label}]loudnorm=I={LOUDNESS}:TP={TRUE_PEAK}:LRA=11:print_format=json[m]"
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
                           "-filter_complex", graph, "-map", "[m]", "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = proc.stderr
    try:
        m = json.loads(text[text.rindex("{"):text.rindex("}") + 1])
        values = {k: float(m[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    except (ValueError, KeyError):
        return None
    if values["input_i"] < -60:  # silêncio: não há o que nivelar
        return None
    return (f"loudnorm=I={LOUDNESS}:TP={TRUE_PEAK}:LRA=11:measured_I={values['input_i']}:"
            f"measured_TP={values['input_tp']}:measured_LRA={values['input_lra']}:"
            f"measured_thresh={values['input_thresh']}:offset={values['target_offset']}:linear=true,aresample=48000")


def render_clip(src: Path, meta: dict, clip: dict, out_dir: Path, words: list[dict], opts: dict,
                keep: list[tuple[float, float]] | None = None, zooms: list[float] | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = float(clip["start"]), float(clip["end"])
    dur = end - start
    w, h = meta["width"], meta["height"]
    has_audio = bool(meta.get("has_audio"))
    tm = TimeMap(keep or [(0.0, dur)])
    out_dur = tm.duration

    is_landscape = w / h > 0.6
    layout = opts.get("layout") or "auto"
    crop_x = min(1.0, max(0.0, float(opts.get("crop_x", 0.5))))
    if not is_landscape:
        layout = "fit"  # vídeo vertical já está no formato certo

    boxes: list = []
    face_ratio = 0.0
    if layout in ("auto", "face", "split") or zooms:
        boxes = reframe.face_positions(src, start, dur, w, h)
        face_ratio = sum(b is not None for b in boxes) / len(boxes) if boxes else 0.0
    if layout == "auto":
        layout = "face" if face_ratio >= 0.35 else "fit"
    if layout == "split" and not reframe.face_box(boxes):
        layout = "fit"  # sem rosto não há webcam para o painel de cima
    face = reframe.face_box(boxes)

    # ---- legendas no tempo do vídeo final
    inside = [x for x in captions.apply_fixes(words, opts.get("word_fixes"), opts.get("word_adds"))
              if x["e"] > start and x["s"] < end]
    mapped = [{**x, "s": tm.to_out(x["s"] - start), "e": tm.to_out(x["e"] - start)} for x in inside]
    cap_opts = {**opts, "word_fixes": None, "word_adds": None}
    subs = ""
    if (opts.get("captions", True) and mapped) or opts.get("title_overlay"):
        (out_dir / "subs.ass").write_text(captions.build_ass(mapped, 0.0, out_dur, cap_opts), encoding="utf-8")
        subs = ",subtitles=subs.ass"
    (out_dir / "clip.srt").write_text(captions.build_srt(mapped, 0.0, out_dur, cap_opts), encoding="utf-8")

    # ---- jump cuts: concatena os trechos mantidos (vídeo aqui; o áudio em _audio_parts)
    pre = ""
    vin = "0:v"
    if tm.trimmed:
        parts = [f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]" for i, (a, b) in enumerate(tm.keep)]
        parts.append("".join(f"[v{i}]" for i in range(len(tm.keep))) + f"concat=n={len(tm.keep)}:v=1:a=0[vin]")
        pre = ";".join(parts) + ";"
        vin = "vin"

    # ---- áudio: mesmos cortes e, se ligado, volume padrão das redes (-14 LUFS, pico -1 dB)
    voice = bool(opts.get("voice_enhance", True))
    audio_pre, ain = _audio_parts(tm, voice) if has_audio else ("", "")
    level = _loudnorm(src, start, dur, tm, voice) if has_audio and opts.get("normalize_audio", True) else None
    if level:
        audio_pre += f"[{ain}]{level}[aout];"
        ain = "aout"
    pre += audio_pre

    # ---- layout → [lay], e onde o rosto fica no quadro final (para mirar o zoom)
    crop_w, crop_h = _even(h * 9 / 16), _even(h)
    face_out = (0.5, 0.5)
    if layout == "face":
        path, _ = reframe.camera_path(boxes)
        script, x0 = reframe.sendcmd_script(path, w, crop_w, out_dur, to_src=tm.to_src)
        (out_dir / "cmds.txt").write_text(script, encoding="utf-8")
        send = "sendcmd=f=cmds.txt," if script else ""
        chain = f"[{vin}]{send}crop={crop_w}:{crop_h}:{x0}:0,scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1[lay]"
        if face:
            face_out = (0.5, face[1])
    elif layout == "manual":
        x0 = int(min(max(crop_x * w - crop_w / 2, 0), w - crop_w))
        chain = f"[{vin}]crop={crop_w}:{crop_h}:{x0}:0,scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1[lay]"
        if face:
            face_out = (min(max((face[0] * w - x0) / crop_w, 0), 1), face[1])
    elif layout == "split":
        cx, cy, fw = face
        top_aspect = OUT_W / SPLIT_TOP
        cam_w = min(max(fw * w * 4.0, w * 0.18), w * 0.6, h * top_aspect)
        cam_h = cam_w / top_aspect
        cam_w, cam_h = _even(cam_w), _even(cam_h)
        cam_x = int(min(max(cx * w - cam_w / 2, 0), w - cam_w))
        cam_y = int(min(max(cy * h - cam_h * 0.45, 0), h - cam_h))
        game_h = OUT_H - SPLIT_TOP
        game_w = _even(min(w, h * OUT_W / game_h))
        game_x = int(min(max(crop_x * w - game_w / 2, 0), w - game_w))
        chain = (
            f"[{vin}]split=2[c][g];"
            f"[c]crop={cam_w}:{cam_h}:{cam_x}:{cam_y},scale={OUT_W}:{SPLIT_TOP}:flags=lanczos[top];"
            f"[g]crop={game_w}:{_even(h)}:{game_x}:0,scale={OUT_W}:{game_h}:flags=lanczos[bot];"
            f"[top][bot]vstack,setsar=1[lay]"
        )
        face_out = ((cx * w - cam_x) / cam_w, (cy * h - cam_y) / cam_h * SPLIT_TOP / OUT_H)
    else:
        layout = "fit"
        chain = (
            f"[{vin}]split=2[a][b];"
            f"[a]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
            f"gblur=sigma=30,eq=brightness=-0.08[bg];"
            f"[b]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[lay]"
        )
        if face:
            scale = min(OUT_W / w, OUT_H / h)
            fg_w, fg_h = w * scale, h * scale
            face_out = (((OUT_W - fg_w) / 2 + face[0] * fg_w) / OUT_W, ((OUT_H - fg_h) / 2 + face[1] * fg_h) / OUT_H)

    # ---- zoom de reação: corte seco para um quadro mais fechado no rosto
    last = "lay"
    zoom_windows = []
    for z in sorted(zooms or []):
        t = tm.to_out(z - start)
        if 0 <= t < out_dur - 0.3 and all(abs(t - p) > ZOOM_HOLD + 1.0 for p, _ in zoom_windows):
            zoom_windows.append((t, min(out_dur, t + ZOOM_HOLD)))
    if zoom_windows:
        zw, zh = _even(OUT_W / ZOOM), _even(OUT_H / ZOOM)
        zx = int(min(max(face_out[0] * OUT_W - zw / 2, 0), OUT_W - zw))
        zy = int(min(max(face_out[1] * OUT_H - zh * 0.4, 0), OUT_H - zh))
        enable = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in zoom_windows)
        chain += (f";[lay]split=2[m][z];[z]crop={zw}:{zh}:{zx}:{zy},scale={OUT_W}:{OUT_H}:flags=lanczos[zz];"
                  f"[m][zz]overlay=enable='{enable}'[lz]")
        last = "lz"
    # saída extra: quadros limpos (sem legenda nem título) para escolher a capa, 2 por segundo
    frames_dir = out_dir / "capa" / "quadros"
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    chain += (f";[{last}]split=2[pv][cv];[pv]null{subs}[v];"
              f"[cv]fps={1 / capa.FRAME_STEP:g},scale={capa.FRAME_W}:{capa.FRAME_H}:flags=bicubic[cq]")

    base = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
            "-filter_complex", pre + chain, "-map", "[v]"]
    audio = []
    if has_audio:
        audio = ["-map", ain if ain == "0:a:0" else f"[{ain}]", "-c:a", "aac", "-b:a", "192k"]
    tail = ["-r", f"{min(meta.get('fps') or 30, 60):.3f}", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "clip.mp4",
            "-map", "[cq]", "-q:v", "3", "-start_number", "0", "capa/quadros/%04d.jpg"]
    (out_dir / "sem_capa.mp4").unlink(missing_ok=True)  # capa no fim do vídeo é refeita depois do render
    _run_encode(base, audio + tail, out_dir)

    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(1.0, out_dur / 2):.2f}", "-i", "clip.mp4",
                    "-frames:v", "1", "-vf", "scale=360:-2", "thumb.jpg"], cwd=out_dir, capture_output=True)
    return {
        "layout": layout, "face_ratio": round(face_ratio, 2),
        "out_duration": round(out_dur, 2), "removed": round(dur - out_dur, 2),
        "zooms_applied": len(zoom_windows),
        "audio_normalized": bool(level),
        "voice_enhanced": has_audio and voice,
        "srt": "clip.srt",
        "time_map": [[round(a, 3), round(b, 3)] for a, b in tm.keep],
    }


def proxy(src: Path, meta: dict, start: float, end: float, out: Path) -> Path:
    """Prévia leve (baixa resolução) de um trecho, para o editor tocar no navegador."""
    out.parent.mkdir(parents=True, exist_ok=True)
    vertical = meta["width"] / meta["height"] < 1
    scale = "scale=-2:640" if vertical else "scale=640:-2"
    base = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}",
            "-vf", scale, "-map", "0:v:0"]
    audio = ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "96k"]
    tail = ["-pix_fmt", "yuv420p", "-g", "15", "-movflags", "+faststart", out.name]
    _run_encode(base, audio + tail, out.parent)
    return out
