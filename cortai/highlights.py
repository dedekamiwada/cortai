"""Melhores momentos de uma partida inteira, num vídeo só (short vertical + versão horizontal),
com roteiro de narração para gravar por cima.

Duas bases para achar os lances:
  fala_imagem — transcrição + picos de áudio → candidatos; depois o Claude assiste aos candidatos.
  imagem      — sem transcrição: o Claude varre a partida em folhas de frames (+ picos do som do jogo),
                propõe candidatos e depois assiste a cada um de perto.

Na revisão o Claude escolhe os lances (em ordem cronológica, contando a história da partida), dá um
rótulo curto a cada um, marca quais entram no short e escreve as falas da narração com o segundo
relativo ao início do lance. O código monta as linhas do tempo e o roteiro (.txt e .srt) de cada versão.
"""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from . import frames, picker, render, signals

SHORT_MAX = 90.0          # o short vertical fica em 60–90 s
LABEL_SECONDS = 2.2       # rótulo "#1 · LABEL" no começo de cada lance
SCAN_MAX_FRAMES = 420     # varredura do modo "só imagem": no máximo isso de quadros na partida toda
SCAN_PER_SHEET = 24
WORDS_PER_SECOND = 2.6    # ritmo de narração de short; o roteiro avisa quando uma fala não cabe


# ------------------------------------------------------------------ varredura (modo só imagem)

def scan_sheets(src: Path, meta: dict, out_dir: Path, start: float, end: float,
                max_frames: int = SCAN_MAX_FRAMES, min_step: float = 2.0) -> list[tuple[Path, float, float]]:
    """Folhas de 24 quadros cobrindo o trecho inteiro, espaçadas por igual."""
    dur = end - start
    step = max(min_step, dur / max_frames)
    span = step * SCAN_PER_SHEET
    sheets = []
    for i in range(math.ceil(dur / span)):
        a, b = start + i * span, min(end, start + (i + 1) * span)
        if b - a < step:
            break
        out = out_dir / f"s{i + 1:02d}.jpg"
        frames.grid(src, a, b, out, meta["width"], meta["height"], n=max(2, round((b - a) / step)), cols=6)
        sheets.append((out, a, b))
    return sheets


SCAN_SYSTEM = """Você é editor de vídeos de melhores momentos de partidas de jogos (TikTok/Shorts e YouTube). \
Você vai ver a partida INTEIRA em folhas de quadros (cada quadro tem o segundo do vídeo escrito no canto) e uma \
lista de picos do som (explosões, gols, mortes, gritos). Não há transcrição: decida pelo que se VÊ.

Proponha candidatos a lance para um vídeo de melhores momentos:
- jogadas boas (abate múltiplo, gol, clutch, defesa, virada);
- momentos engraçados (falha, morte boba, bug, situação absurda, reação forte na câmera);
- momentos relevantes para a história da partida (começo, virada no placar, momento decisivo, resultado final).
Cada candidato: `start` alguns segundos antes do acontecimento (só o contexto necessário), `end` logo depois \
que ele resolve, `peak` = segundo do acontecimento. Entre 5 e 30 s cada. Não proponha menu, loja, \
carregamento ou tela parada. Candidatos não podem se sobrepor. `reason` em português diz o que acontece e \
por que entra. `score` 0–100 = força do lance. `title` = rótulo curto do lance."""


def scan_prompt(sheets: list[tuple[Path, float, float]], events: list[dict], count: int, extra: str) -> str:
    parts = [f"Proponha até {count} candidatos."]
    if extra:
        parts.append(f"Pedido do streamer (prioridade máxima): {extra}")
    parts.append("Folhas:")
    parts += [f"- {p.name}: de {a:.0f}s a {b:.0f}s" for p, a, b in sheets]
    peaks = [signals.describe(e) for e in events if e["kind"] in signals.LOUD_KINDS]
    parts += ["", "Picos do som:", "\n".join(peaks[:200]) or "(nenhum)"]
    parts.append('\nResponda somente com JSON {"clips": [{"title", "start", "end", "peak", "hook", "reason", "score", "hashtags"}]}.')
    return "\n".join(parts)


def transcript_candidates_prompt(transcript_text: str, duration: float, count: int, extra: str) -> str:
    parts = [f"Duração total: {duration:.0f}s. Proponha até {count} candidatos a lance para um vídeo de "
             f"MELHORES MOMENTOS da partida (jogadas boas, momentos engraçados e momentos relevantes para a história "
             f"da partida, incluindo o resultado). Cada um entre 5 e 30 s, sem sobreposição."]
    if extra:
        parts.append(f"Instruções extras do usuário: {extra}")
    parts += ["", "<transcricao>", transcript_text, "</transcricao>", "",
              'Responda somente com JSON {"clips": [{"title", "start", "end", "peak", "hook", "reason", "score", "hashtags"}]}.']
    return "\n".join(parts)


# ------------------------------------------------------------------ mapa de partidas da live

MAP_MAX_FRAMES = 480   # live de 2 h → 1 quadro a cada ~15 s
MAP_MIN_STEP = 6.0
MAP_PAD = 8.0          # folga em volta de cada partida (a amostragem é grossa)

MAP_SYSTEM = """Você está mapeando uma live de jogo inteira para separar as PARTIDAS. Você vê a live em folhas \
de quadros (cada quadro tem o segundo do vídeo escrito no canto). Use o HUD do jogo: contagem/início da \
partida (placar zerado, relógio cheio, "3, 2, 1, GO"), placar e relógio durante o jogo, e a tela de fim \
(VITÓRIA/DERROTA, MVP, placar final, "desistência"). Menus, fila, loja, replay de fim e conversa sem jogo \
ficam FORA das partidas.

Para cada partida: `inicio` = primeiro quadro em que ela já está rolando (ou a contagem), `fim` = quadro da \
tela de resultado; `jogo` e `modo` se der para ver (ex.: "Rocket League", "2v2 ranqueada"); `placar` final \
como aparece (ex.: "4 x 2") ou "" se não der para ler; `resultado` do ponto de vista do streamer; `resumo` em \
1 frase do que aconteceu (virada, goleada, prorrogação, desistência…); `destaque` 0–100 = quanto essa partida \
parece render um vídeo de melhores momentos. Partidas em ordem, sem sobreposição. Se a amostragem não \
mostrar o fim exato, estime pelo último quadro com jogo."""

MAP_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["partidas"],
    "properties": {"partidas": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["inicio", "fim", "jogo", "modo", "placar", "resultado", "resumo", "destaque"],
        "properties": {
            "inicio": {"type": "number"}, "fim": {"type": "number"},
            "jogo": {"type": "string"}, "modo": {"type": "string"}, "placar": {"type": "string"},
            "resultado": {"type": "string", "enum": ["vitoria", "derrota", "empate", "desconhecido"]},
            "resumo": {"type": "string"}, "destaque": {"type": "integer"},
        },
    }}},
}


def map_prompt(sheets: list[tuple[Path, float, float]], duration: float) -> str:
    parts = [f"Duração da live: {duration:.0f}s ({duration / 60:.0f} min). Folhas:"]
    parts += [f"- {p.name}: de {a:.0f}s a {b:.0f}s" for p, a, b in sheets]
    parts.append("\nListe todas as partidas.")
    return "\n".join(parts)


def normalize_matches(raw: dict, duration: float) -> list[dict]:
    out = []
    for m in sorted(raw.get("partidas") or [], key=lambda x: float(x.get("inicio", 0))):
        a = max(0.0, float(m.get("inicio", 0)) - MAP_PAD)
        b = min(duration, float(m.get("fim", 0)) + MAP_PAD)
        if b - a < 30:
            continue
        if out and a < out[-1]["fim"]:
            a = out[-1]["fim"]
            if b - a < 30:
                continue
        out.append({
            "n": len(out) + 1, "inicio": round(a, 1), "fim": round(b, 1),
            "jogo": (m.get("jogo") or "").strip(), "modo": (m.get("modo") or "").strip(),
            "placar": (m.get("placar") or "").strip(), "resultado": m.get("resultado") or "desconhecido",
            "resumo": (m.get("resumo") or "").strip(), "destaque": max(0, min(100, int(m.get("destaque") or 0))),
        })
    return out


# ------------------------------------------------------------------ revisão final (escolha + roteiro)

REVIEW_SYSTEM = """Você é o editor-chefe montando UM vídeo de melhores momentos de uma partida, que sai em duas \
versões: um SHORT vertical de 60 a 90 s e um vídeo HORIZONTAL de 3 a 8 min para o YouTube. Para cada \
candidato há uma imagem em grade com quadros do trecho (o segundo do vídeo original está em cada quadro).

Monte a história da partida: começo → lances → momento decisivo → resultado. Para cada candidato:
1. `visual`: o que realmente acontece na tela, em 1–2 frases.
2. `keep=false` se não sustenta: menu, carregamento, tela parada, lance fraco ou repetido.
3. Ajuste `start`/`end` pelo que se vê: só o contexto necessário antes, termine logo que o lance resolve \
(ou quando a reação acaba). Dentro do short, prefira lances de 5 a 15 s.
4. `label`: rótulo de 1–4 palavras em caixa alta para a tela ("CLUTCH 1V3", "MORRI PRA PORTA", "VIRADA").
5. `tipo`: jogada | engracado | relevante | resultado.
6. `no_short`: true para os lances que entram no short vertical. A soma das durações do short deve ficar \
entre 60 e 90 s; o horizontal usa todos com keep=true.
7. `reactions`: até 2 segundos (vídeo original) em que a cara do streamer reage forte (zoom no short).
8. `narracao`: falas para o streamer gravar por cima, com `t` = segundos desde o INÍCIO do lance (já \
ajustado) e `texto`. Tom de narrador de shorts brasileiro: frases curtas, zoeira, conta o que vai acontecer \
sem estragar a surpresa e comenta depois. No máximo ~2,5 palavras por segundo do espaço até a próxima fala. \
Deixe o som do jogo/a reação respirar no pico (não narre por cima do momento principal nem de uma fala \
engraçada do streamer).
Além disso: `titulo` do vídeo (curto, específico), `gancho` = primeira fala da narração do short (prende nos \
3 primeiros segundos sem entregar o final) e `post` = descrição com 3 a 5 hashtags."""

REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["titulo", "gancho", "post", "lances"],
    "properties": {
        "titulo": {"type": "string"},
        "gancho": {"type": "string"},
        "post": {"type": "string"},
        "lances": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["candidate", "keep", "start", "end", "peak", "label", "tipo", "visual", "no_short",
                         "reactions", "score", "narracao"],
            "properties": {
                "candidate": {"type": "integer"},
                "keep": {"type": "boolean"},
                "start": {"type": "number"},
                "end": {"type": "number"},
                "peak": {"type": "number"},
                "label": {"type": "string"},
                "tipo": {"type": "string", "enum": ["jogada", "engracado", "relevante", "resultado"]},
                "visual": {"type": "string"},
                "no_short": {"type": "boolean"},
                "reactions": {"type": "array", "items": {"type": "number"}},
                "score": {"type": "integer"},
                "narracao": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["t", "texto"],
                    "properties": {"t": {"type": "number"}, "texto": {"type": "string"}},
                }},
            },
        }},
    },
}


def review_prompt(candidates, windows, images, transcript_text_for, extra: str) -> str:
    parts = []
    if extra:
        parts.append(f"Pedido do streamer (prioridade máxima): {extra}\n")
    for i, (c, (ws, we), img) in enumerate(zip(candidates, windows, images), 1):
        parts.append(f"## Candidato {i} — imagem {img} (quadros de {ws:.1f}s a {we:.1f}s)")
        parts.append(f"Proposta: \"{c.title}\" de {c.start:.2f}s a {c.end:.2f}s, pico em {c.peak or c.start:.2f}s. {c.reason}")
        text = transcript_text_for(ws, we)
        if text:
            parts.append(text)
        parts.append("")
    parts.append("Responda com JSON com um item em `lances` por candidato.")
    return "\n".join(parts)


def normalize(raw: dict, candidates, duration: float) -> dict:
    """Lances aceitos em ordem cronológica, sem sobreposição; short cortado em SHORT_MAX."""
    lances = []
    for item in raw.get("lances") or []:
        idx = item.get("candidate")
        if not item.get("keep") or not isinstance(idx, int) or not 1 <= idx <= len(candidates):
            continue
        start = max(0.0, float(item.get("start", 0)))
        end = min(duration, float(item.get("end", 0)))
        if end - start < 2.0:
            continue
        if any(start < o["end"] and end > o["start"] for o in lances):
            continue
        dur = end - start
        fala = []
        for n in sorted(item.get("narracao") or [], key=lambda x: float(x.get("t", 0))):
            text = " ".join((n.get("texto") or "").split())
            t = float(n.get("t", 0))
            if text and 0 <= t < dur:
                fala.append({"t": round(t, 2), "texto": text})
        lances.append({
            "candidate": idx, "start": round(start, 2), "end": round(end, 2),
            "peak": float(item.get("peak") or start),
            "label": " ".join((item.get("label") or "").upper().split())[:28],
            "tipo": item.get("tipo") or "jogada", "visual": (item.get("visual") or "").strip(),
            "no_short": bool(item.get("no_short")), "score": max(0, min(100, int(item.get("score") or 50))),
            "reactions": [float(r) for r in item.get("reactions") or [] if start <= float(r) <= end][:2],
            "narracao": fala,
        })
    lances.sort(key=lambda x: x["start"])
    # o short respeita o teto: tira os mais fracos marcados até caber
    short = [x for x in lances if x["no_short"]] or lances[:]
    while sum(x["end"] - x["start"] for x in short) > SHORT_MAX and len(short) > 1:
        short.remove(min(short, key=lambda x: x["score"]))
    for x in lances:
        x["no_short"] = x in short
    return {"titulo": (raw.get("titulo") or "").strip(), "gancho": (raw.get("gancho") or "").strip(),
            "post": (raw.get("post") or "").strip(), "lances": lances}


# ------------------------------------------------------------------ montagem

def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _labels_ass(width: int, height: int, items: list[tuple[float, str]], title: str) -> str:
    vertical = height > width
    size = 64 if vertical else 54
    # vertical: a câmera e as faixas da cena ficam no terço de cima; rótulo e título vão para a área do jogo
    y = int(height * (0.53 if vertical else 0.12))
    title_y = int(height * (0.66 if vertical else 0.5))
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Tag,Arial Black,{size},&H00FFFFFF,&H000000FF,&H001A0EE1,&H001A0EE1,-1,0,0,0,100,100,1,0,3,16,0,5,0,0,0,1
Style: Titulo,Arial Black,{size + 30},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,1,0,1,9,0,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if title:
        # na tela vai só o título em si; "| Rocket League" e afins ficam para o título do post
        clean = title.split(" | ")[0].replace("{", "").replace("}", "").replace("\\", "").upper()
        lines.append(f"Dialogue: 2,{_ass_time(0)},{_ass_time(2.0)},Titulo,,0,0,0,,"
                     f"{{\\an5\\pos({width // 2},{title_y})\\fscx40\\fscy40\\t(0,150,\\fscx100\\fscy100)\\fad(0,200)}}{clean}")
    for t, text in items:
        clean = text.replace("{", "").replace("}", "").replace("\\", "")
        lines.append(f"Dialogue: 1,{_ass_time(t + (2.0 if t == 0 and title else 0))},"
                     f"{_ass_time(t + LABEL_SECONDS + (2.0 if t == 0 and title else 0))},Tag,,0,0,0,,"
                     f"{{\\an5\\pos({width // 2},{y})\\fscx30\\fscy30\\t(0,120,\\fscx100\\fscy100)\\fad(0,150)}}{clean}")
    return head + "\n".join(lines) + "\n"


def horizontal_segment(src: Path, meta: dict, start: float, end: float, out: Path, voice: bool) -> Path:
    """Trecho 1920x1080 (o vídeo inteiro, com barras desfocadas se não for 16:9) com a voz tratada."""
    vf = ("split=2[a][b];[a]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,gblur=sigma=30,"
          "eq=brightness=-0.08[bg];[b]scale=1920:1080:force_original_aspect_ratio=decrease[fg];"
          "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    base = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}",
            "-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]"]
    audio = []
    if meta.get("has_audio"):
        audio = ["-map", "0:a:0", "-af", (render.VOICE_CHAIN + "," if voice else "") + "aresample=48000",
                 "-c:a", "aac", "-b:a", "192k"]
    tail = ["-r", f"{min(meta.get('fps') or 30, 60):.3f}", "-pix_fmt", "yuv420p", out.name]
    out.parent.mkdir(parents=True, exist_ok=True)
    render._run_encode(base, audio + tail, out.parent)
    return out


def _duration(path: Path) -> float:
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True)
    return float(proc.stdout.strip() or 0)


def compile_video(segments: list[Path], labels: list[str], title: str, out: Path, width: int, height: int,
                  fps: float) -> list[float]:
    """Junta os trechos com corte seco + flash branco curto, rótulo de cada lance e título no começo.
    Áudio final a -14 LUFS (2 passadas). Devolve o início de cada trecho no vídeo final."""
    work = out.parent
    starts, acc = [], 0.0
    for s in segments:
        starts.append(acc)
        acc += _duration(s)
    (work / f"{out.stem}.ass").write_text(
        _labels_ass(width, height, list(zip(starts, labels)), title), encoding="utf-8")
    inputs, chain = [], []
    for i, s in enumerate(segments):
        inputs += ["-i", str(s)]
        chain.append(f"[{i}:v]scale={width}:{height},fps={fps:.3f},format=yuv420p,setsar=1[v{i}];"
                     f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
    cat = "".join(f"[v{i}][a{i}]" for i in range(len(segments)))
    flashes = "".join(f",drawbox=x=0:y=0:w=iw:h=ih:color=white@0.6:t=fill:enable='between(t,{t:.3f},{t + 0.1:.3f})'"
                      for t in starts[1:])
    graph = ";".join(chain) + f";{cat}concat=n={len(segments)}:v=1:a=1[cv][ca];[cv]subtitles={out.stem}.ass{flashes}[v]"
    tmp = work / f"{out.stem}.tmp.mkv"
    base = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[ca]"]
    render._run_encode(base, ["-c:a", "pcm_s16le", "-pix_fmt", "yuv420p", tmp.name], work)
    master = _master(tmp)
    proc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", tmp.name, "-map", "0:v", "-map", "0:a", "-c:v", "copy",
                           "-af", master, "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-movflags", "+faststart",
                           out.name], cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace")
    tmp.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou ao finalizar {out.name}: {proc.stderr[-1200:]}")
    return starts


def _master(path: Path) -> str:
    pre = "alimiter=limit=0.8:attack=3:release=80:level=disabled"
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
                           f"{pre},loudnorm=I=-14:TP=-2:LRA=11:print_format=json", "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = proc.stderr
    try:
        m = json.loads(text[text.rindex("{"):text.rindex("}") + 1])
        if float(m["input_i"]) < -60:
            return pre
        return (f"{pre},loudnorm=I=-14:TP=-2:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
                f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:"
                f"linear=true,aresample=48000")
    except (ValueError, KeyError):
        return pre


# ------------------------------------------------------------------ roteiro de narração

def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def script(lances: list[dict], starts: list[float], durations: list[float], total: float, gancho: str,
           titulo: str, version: str) -> tuple[str, str]:
    """Roteiro (.txt legível) e .srt com as falas no tempo do vídeo final, para gravar no CapCut."""
    lines: list[tuple[float, float, str, int]] = []
    if gancho:
        lines.append((0.0, 0.0, gancho, 0))
    for k, (lance, s0, dur) in enumerate(zip(lances, starts, durations), 1):
        for n in lance.get("narracao") or []:
            lines.append((s0 + n["t"], 0.0, n["texto"], k))
    lines.sort(key=lambda x: x[0])
    # o Claude às vezes repete o gancho como primeira fala do 1º lance: fica uma só
    seen, unique = set(), []
    for line in lines:
        key = " ".join(line[2].lower().split())
        if line[0] < 2.0 and key in seen:
            continue
        seen.add(key)
        unique.append(line)
    lines = unique
    # cada fala vai até a próxima (ou o fim), no máximo o tempo que ela pede
    timed = []
    for i, (t, _, text, k) in enumerate(lines):
        nxt = lines[i + 1][0] if i + 1 < len(lines) else total
        need = max(1.2, len(text.split()) / WORDS_PER_SECOND)
        timed.append((t, min(nxt, t + need), text, k, need > nxt - t + 0.05))
    head = [f"ROTEIRO DE NARRAÇÃO — {version}", f"Vídeo: {titulo}" if titulo else "", f"Duração: {total:.1f} s", ""]
    body = []
    current = -1
    for t, e, text, k, tight in timed:
        if k != current:
            current = k
            if k:
                lance = lances[k - 1]
                # lances do job guardam o rótulo em `title` (é o título do clipe no editor)
                body.append(f"\n#{k} {lance.get('title', '')}  ({starts[k - 1]:.1f}s → {starts[k - 1] + durations[k - 1]:.1f}s)")
                body.append(f"   na tela: {lance.get('visual', '')}")
            else:
                body.append("\nGANCHO")
        body.append(f"   [{t:5.1f}s] {text}" + ("   ⚠ fala longa para o espaço: fale rápido ou corte" if tight else ""))
    txt = "\n".join(x for x in head if x is not None) + "\n".join(body) + "\n"
    srt = "\n".join(f"{i}\n{_srt_time(t)} --> {_srt_time(e)}\n{text}\n" for i, (t, e, text, _, _) in enumerate(timed, 1))
    return txt, srt
