"""Eventos do jogo lidos no HUD (Rocket League): gols, início e fim de partida, com tempo de ~2 s.

Medido numa live real do Dede (HEVC 1080x1920, keyframe a cada 2 s):
- decodificar só os keyframes com a GPU, recortando a faixa do placar: 7 min de partida em ~1,5 s;
- o RELÓGIO PARA em todo gol (replay até o kickoff), no começo da partida, no fim do tempo e no fim do jogo;
  em 3 partidas (21 pausas) toda pausa caiu num desses casos — é o sinal de tempo, barato e exato;
- dizer QUEM marcou comparando pixels dos dígitos foi frágil (caixa amarelo-clara, animação do replay,
  "2" e "3" parecidos), então a LEITURA do placar fica com o Claude: uma folha com o recorte do placar antes
  e durante cada pausa, numa chamada só.
- nas 3 partidas, o time do streamer estava sempre à ESQUERDA do placar.

O local do placar na tela é calibrado uma vez por vídeo (o Claude marca as caixas num quadro).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

from . import picker

STEP = 2.0              # keyframes do OBS: 1 a cada 2 s
MIN_FREEZE = 3          # keyframes iguais seguidos (6 s) = relógio parado
CLOCK_SAME = 0.92       # IoU da máscara do relógio para "mesmo valor"


# ------------------------------------------------------------------ calibração (onde está o placar)

CALIBRATE_SYSTEM = """Você localiza o placar de Rocket League num quadro de live. O placar fica no topo da área \
do jogo: uma caixa colorida com o número de gols do time da esquerda, o relógio no meio e outra caixa colorida \
com os gols do time da direita. Devolva as caixas em PIXELS do quadro (x, y = canto superior esquerdo; w, h = \
largura e altura), justas em volta de cada elemento. `visivel=false` se não houver placar no quadro."""

CALIBRATE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["visivel", "esquerda", "relogio", "direita"],
    "properties": {
        "visivel": {"type": "boolean"},
        **{k: {"type": "object", "additionalProperties": False, "required": ["x", "y", "w", "h"],
               "properties": {c: {"type": "integer"} for c in ("x", "y", "w", "h")}}
           for k in ("esquerda", "relogio", "direita")},
    },
}


def _frame(src: Path, t: float, out: Path) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1", str(out)],
                   capture_output=True)
    return out


def _box_sat(img: np.ndarray) -> float:
    return float(np.median(cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[..., 1])) if img.size else 0.0


def calibrate(backend: str, src: Path, meta: dict, times: list[float], work: Path) -> dict | None:
    """Pergunta ao Claude onde está o placar em quadros de partida e confere pelas cores das caixas."""
    work.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(times):
        img = _frame(src, t, work / f"calib-{i}.jpg")
        if not img.exists():
            continue
        raw = picker.ask(backend, CALIBRATE_SYSTEM,
                         f"Quadro de {meta['width']}x{meta['height']} px. Onde estão as caixas do placar?",
                         CALIBRATE_SCHEMA, [img])
        if not raw.get("visivel"):
            continue
        boxes = {k: [int(raw[k][c]) for c in ("x", "y", "w", "h")] for k in ("esquerda", "relogio", "direita")}
        frame = cv2.imdecode(np.fromfile(str(img), np.uint8), cv2.IMREAD_COLOR)
        sats = [_box_sat(_crop(frame, boxes[k], 0)) for k in ("esquerda", "direita")]
        if all(s > 60 for s in sats):  # as duas caixas são coloridas: a marcação faz sentido
            return {"boxes": boxes, "frame_w": meta["width"], "frame_h": meta["height"]}
    return None


# ------------------------------------------------------------------ varredura (pausas do relógio)

def _crop(frame: np.ndarray, box: list[int], y0: int) -> np.ndarray:
    x, y, w, h = box
    return frame[max(0, y - y0): y - y0 + h, max(0, x): x + w]


def _digits(img: np.ndarray) -> np.ndarray:
    """Pixels "brancos" do elemento (menor canal RGB alto), com limiar adaptativo: cada partida tem cores diferentes."""
    if img.size == 0:
        return np.zeros((1, 1), np.uint8)
    white = img.min(axis=2)
    thr, _ = cv2.threshold(white, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return (white > max(thr, 120)).astype(np.uint8)


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return 0.0
    u = (a | b).sum()
    return 1.0 if u == 0 else float((a & b).sum()) / float(u)


def _band(src: Path, start: float, end: float, y0: int, h: int, width: int) -> np.ndarray:
    """Só a faixa do placar de cada keyframe (GPU quando houver; senão CPU)."""
    base = ["-skip_frame", "nokey", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(src),
            "-vf", f"crop={width}:{h}:0:{y0}", "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    raw = b""
    for hw in (["-hwaccel", "cuda"], []):
        proc = subprocess.run(["ffmpeg", "-v", "error", *hw, *base], capture_output=True)
        raw = proc.stdout
        if proc.returncode == 0 and raw:
            break
    size = width * h * 3
    n = len(raw) // size
    return np.frombuffer(raw[: n * size], np.uint8).reshape(n, h, width, 3)


CHUNK = 600.0  # s por bloco de decodificação: a live inteira não cabe na memória (2 h ≈ 1,5 GB de faixas)


HUD_GAP = 10  # keyframes (20 s) sem placar = saiu da partida (menu, fila, tela de resultado)


def _spans(ok: list[bool], start: float) -> list[list[float]]:
    """Trechos contínuos com o placar na tela (≈ uma partida cada), tolerando buracos curtos (replay, explosão)."""
    spans, cur, gap = [], None, 0
    for i, v in enumerate(ok):
        if v:
            if cur is None:
                cur = [i, i]
            cur[1], gap = i, 0
        elif cur is not None:
            gap += 1
            if gap > HUD_GAP:
                spans.append(cur)
                cur, gap = None, 0
    if cur is not None:
        spans.append(cur)
    return [[round(start + a * STEP, 1), round(start + (b + 1) * STEP, 1)] for a, b in spans if b - a >= 15]


def freezes(src: Path, meta: dict, calib: dict, start: float, end: float) -> tuple[list[dict], list[np.ndarray], list]:
    """Pausas do relógio com o HUD visível. Devolve (pausas, recortes do placar em meia resolução, trechos com HUD)."""
    boxes = calib["boxes"]
    top = min(b[1] for b in boxes.values()) - 6
    bottom = max(b[1] + b[3] for b in boxes.values()) + 6
    y0, h = max(0, top), min(meta["height"], bottom) - max(0, top)
    h -= h % 2
    x0 = max(0, boxes["esquerda"][0] - 8)
    x1 = min(meta["width"], boxes["direita"][0] + boxes["direita"][2] + 8)
    ok: list[bool] = []
    clock: list[np.ndarray] = []
    crops: list[np.ndarray] = []
    t = start
    while t < end - 0.5:
        stop = min(end, t + CHUNK)
        bands = _band(src, t, stop, y0, h, meta["width"])
        for f in bands:
            ok.append(all(_box_sat(_crop(f, boxes[k], y0)) > 60 for k in ("esquerda", "direita")))
            clock.append(_digits(_crop(f, boxes["relogio"], y0)))
            sc = f[:, x0:x1]
            crops.append(cv2.resize(sc, (sc.shape[1] // 2, sc.shape[0] // 2), interpolation=cv2.INTER_AREA))
        # o próximo bloco começa no keyframe seguinte (blocos de duração múltipla de STEP)
        t = start + len(ok) * STEP
        if len(bands) == 0:
            break
    runs, i = [], 1
    while i < len(ok):
        if ok[i] and ok[i - 1] and clock[i].sum() > 40 and _iou(clock[i], clock[i - 1]) > CLOCK_SAME:
            j = i
            while j + 1 < len(ok) and ok[j + 1] and _iou(clock[j + 1], clock[i]) > CLOCK_SAME:
                j += 1
            if j - i + 2 >= MIN_FREEZE:
                # oscilação do relógio parte uma pausa em duas: junta só se o relógio mostra o MESMO valor
                # (gol 4 s depois do kickoff existe — aconteceu na live medida)
                if runs and i - 1 - runs[-1][1] <= 2 and _iou(clock[runs[-1][0]], clock[i]) > CLOCK_SAME:
                    runs[-1] = (runs[-1][0], j)
                else:
                    runs.append((i - 1, j))
            i = j + 1
        else:
            i += 1
    out = []
    for a, b in runs:
        pre = next((k for k in range(a - 1, max(-1, a - 4), -1) if ok[k]), None)
        out.append({"t": round(start + a * STEP, 1), "fim_pausa": round(start + (b + 1) * STEP, 1),
                    "i_pre": pre, "i_dur": b, "hud_antes": pre is not None})
    return out, crops, _spans(ok, start)


# ------------------------------------------------------------------ leitura do placar (Claude)

READ_SYSTEM = """Você lê placares de Rocket League. Cada item da folha tem um número e dois recortes do placar: \
ANTES (alguns segundos antes de o relógio parar) e DURANTE (com o relógio parado). Em cada recorte há o número de \
gols da caixa da esquerda, o relógio e o número da caixa da direita. Leia exatamente o que aparece. Se o relógio \
mostrar texto (ex.: PRORROGAÇÃO) ou "+0:12", copie como está. Use null quando não der para ler."""

READ_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["itens"],
    "properties": {"itens": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["n", "antes_esq", "antes_dir", "antes_relogio", "dur_esq", "dur_dir", "dur_relogio"],
        "properties": {
            "n": {"type": "integer"},
            **{k: {"type": ["integer", "null"]} for k in ("antes_esq", "antes_dir", "dur_esq", "dur_dir")},
            **{k: {"type": ["string", "null"]} for k in ("antes_relogio", "dur_relogio")},
        },
    }}},
}

PER_SHEET = 24


def _sheets(items: list[dict], crops: list[np.ndarray], work: Path) -> list[Path]:
    paths = []
    for s0 in range(0, len(items), PER_SHEET):
        tiles = []
        for it in items[s0:s0 + PER_SHEET]:
            dur = crops[it["i_dur"]]
            pre = crops[it["i_pre"]] if it["i_pre"] is not None else np.zeros_like(dur)
            pair = np.hstack([pre, np.full((dur.shape[0], 10, 3), 40, np.uint8), dur])
            pair = cv2.resize(pair, (pair.shape[1] * 2, pair.shape[0] * 2), interpolation=cv2.INTER_CUBIC)
            label = np.full((26, pair.shape[1], 3), 20, np.uint8)
            cv2.putText(label, f"#{it['n']}   ANTES  |  DURANTE", (6, 19), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 230, 255), 2)
            tiles.append(np.vstack([label, pair]))
        out = work / f"placar-{s0 // PER_SHEET + 1}.jpg"
        ok, buf = cv2.imencode(".jpg", np.vstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 88])
        out.write_bytes(buf.tobytes())
        paths.append(out)
    return paths


def read_events(backend: str, src: Path, meta: dict, calib: dict, start: float, end: float,
                work: Path) -> tuple[list[dict], list]:
    """Pausas do relógio → eventos (gol do streamer, gol do adversário, início, fim do tempo, fim) com placar."""
    work.mkdir(parents=True, exist_ok=True)
    items, crops, spans = freezes(src, meta, calib, start, end)
    if not items:
        return [], spans
    for n, it in enumerate(items, 1):
        it["n"] = n
    sheets = _sheets(items, crops, work)
    raw = picker.ask(backend, READ_SYSTEM, f"Leia os {len(items)} itens das folhas.", READ_SCHEMA, sheets)
    reads = {r["n"]: r for r in raw.get("itens") or []}
    return classify(items, reads), spans


def _step(prev: tuple, cur: tuple) -> str | None:
    if cur == (prev[0] + 1, prev[1]):
        return "gol_nosso"   # time do streamer = esquerda (medido nas 3 partidas da live de teste)
    if cur == (prev[0], prev[1] + 1):
        return "gol_deles"
    return None


def classify(items: list[dict], reads: dict) -> list[dict]:
    """Classifica cada pausa pela SEQUÊNCIA dos placares lidos durante as pausas.

    Comparar com o recorte "antes" falhou em 6 de 22 gols na live de teste: o placar muda no mesmo instante em
    que o relógio para, e o keyframe "antes" às vezes já mostra o placar novo. Comparando cada pausa com a
    anterior, as mesmas leituras deram 22/22. A leitura "antes" só é usada na primeira pausa de um trecho.
    """
    events, last = [], None
    for it in items:
        r = reads.get(it["n"]) or {}
        d = (r.get("dur_esq"), r.get("dur_dir"))
        a = (r.get("antes_esq"), r.get("antes_dir"))
        clock = (r.get("dur_relogio") or "").strip()
        kind = "pausa"
        if None not in d:
            if last is not None and (d[0] < last[0] or d[1] < last[1]):
                last = (0, 0)  # placar diminuiu: partida nova (a varredura cobre a live inteira)
            if d == (0, 0):
                kind = "inicio"
            elif last is not None and _step(last, d):
                kind = _step(last, d)
            elif last is None and None not in a and _step(a, d):
                kind = _step(a, d)
            elif last == d and clock in ("0:00", "00:00"):
                kind = "fim_do_tempo"
            last = d
        elif not it["hud_antes"]:
            last = None  # HUD sumiu: provável troca de partida
        events.append({"t": it["t"], "fim_pausa": it["fim_pausa"], "tipo": kind,
                       "placar": f"{d[0]} x {d[1]}" if None not in d else "", "relogio": clock})
    return events
