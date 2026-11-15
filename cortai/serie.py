"""A série do resumo narrado como produto: episódios, placar da campanha e meta ("até pegar Champion").

O bordão e o número do próximo episódio continuam nas preferências (jobs._reserve_episode); aqui fica o
histórico: cada resumo pronto registra seu episódio (adversário, placar final lido no placar do jogo, resultado).
O roteiro do episódio seguinte recebe a campanha em uma linha (poucos tokens).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

_lock = threading.Lock()


def _path(root: Path) -> Path:
    return root / "_serie" / "serie.json"


def load(root: Path) -> dict:
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("meta", "")
    data.setdefault("episodios", [])
    return data


def _write(root: Path, data: dict) -> None:
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def set_goal(root: Path, meta: str) -> dict:
    with _lock:
        data = load(root)
        data["meta"] = (meta or "").strip()[:120]
        _write(root, data)
        return data


def final_score(events: list[dict], start: float, end: float) -> tuple[str, str]:
    """Placar final e resultado do streamer (time da esquerda) pelo último placar lido na partida."""
    scores = [e.get("placar") for e in events if start <= e["t"] <= end and e.get("placar")]
    if not scores:
        return "", "desconhecido"
    try:
        a, b = (int(x) for x in scores[-1].split(" x "))
    except ValueError:
        return scores[-1], "desconhecido"
    return f"{a} x {b}", "vitoria" if a > b else "derrota" if a < b else "empate"


def register(root: Path, job_id: str, n: int, bordao: str, adversario: str, placar: str, resultado: str,
             titulo: str) -> None:
    """Grava (ou atualiza, se o resumo for remontado) o episódio do projeto."""
    with _lock:
        data = load(root)
        eps = [e for e in data["episodios"] if e.get("job") != job_id]
        eps.append({"n": int(n), "job": job_id, "bordao": bordao, "adversario": adversario, "placar": placar,
                    "resultado": resultado, "titulo": titulo, "data": round(time.time())})
        data["episodios"] = sorted(eps, key=lambda e: (e["n"], e["data"]))
        _write(root, data)


def record(data: dict) -> dict:
    eps = data["episodios"]
    return {"vitorias": sum(e["resultado"] == "vitoria" for e in eps),
            "derrotas": sum(e["resultado"] == "derrota" for e in eps), "episodios": len(eps)}


def context(root: Path, exclude_job: str | None = None) -> str:
    """Uma linha de campanha para o roteiro do próximo episódio ("" sem histórico)."""
    data = load(root)
    eps = [e for e in data["episodios"] if e.get("job") != exclude_job]
    if not eps and not data["meta"]:
        return ""
    r = record({"episodios": eps})
    parts = []
    if eps:
        last = eps[-1]
        nome = {"vitoria": "vitória", "derrota": "derrota", "empate": "empate"}.get(last["resultado"], "")
        parts.append(f"Campanha até aqui: {r['vitorias']}V {r['derrotas']}D em {len(eps)} episódio{'s' if len(eps) != 1 else ''}"
                     + (f"; no último, {nome} {last['placar']}" if nome else "")
                     + (f" contra {last['adversario']}" if last.get("adversario") else ""))
    if data["meta"]:
        parts.append(f"meta da série: {data['meta']}")
    return ". ".join(parts) + ". Cite a campanha no gancho ou no fechamento, em uma frase."
