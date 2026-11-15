"""Relatório do consumo de tokens do Claude (jobs/_uso/uso.csv): por etapa, por projeto e comparado com o
período anterior. Feito para a revisão periódica ler um resumo curto em vez da planilha inteira.

    python -m cortai.uso [dias]     (padrão: 7)
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import paths

# medições feitas ao otimizar (US$ por chamada); servem de alarme quando uma etapa sai muito disso
REFERENCIAS = {
    "placar_ler": 0.0063,      # Haiku low, live de 2,25 h (12,2 mil tokens de entrada)
    "placar_conferir": 0.025,  # releitura só dos itens suspeitos com o modelo principal
    "resumo_pedido": 0.055,    # pedido em texto para mudar o resumo
}
ALERTA_SUBIDA = 0.25     # entrada/chamada 25% maior que no período anterior
ALERTA_REFERENCIA = 1.5  # custo/chamada 1,5x acima da medição de referência
ALERTA_CHAMADA = 60_000  # tokens de entrada numa única chamada


def _f(x: str) -> float:
    try:
        return float(x or 0)
    except ValueError:
        return 0.0


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        out = []
        for r in csv.DictReader(f, delimiter=";"):
            try:
                r["_t"] = datetime.strptime(r["quando"], "%Y-%m-%d %H:%M:%S")
            except (KeyError, TypeError, ValueError):
                continue
            r["_in"] = _f(r.get("entrada")) + _f(r.get("cache_criado")) + _f(r.get("cache_lido"))
            r["_out"] = _f(r.get("saida"))
            r["_usd"] = _f(r.get("custo_usd"))
            out.append(r)
        return out


def _k(n: float) -> str:
    return f"{n / 1000:.1f} mil" if n >= 1000 else f"{n:.0f}"


def _by_step(rows: list[dict]) -> dict[str, dict]:
    steps: dict[str, list[dict]] = {}
    for r in rows:
        steps.setdefault(r.get("etapa") or "?", []).append(r)
    return {s: {"n": len(rs), "in": statistics.median(r["_in"] for r in rs),
                "out": statistics.median(r["_out"] for r in rs),
                "usd": statistics.median(r["_usd"] for r in rs), "total": sum(r["_usd"] for r in rs),
                "modelos": sorted({r.get("modelo", "") for r in rs})} for s, rs in steps.items()}


def _job_info(jobs_dir: Path, job_id: str) -> tuple[str, float]:
    try:
        j = json.loads((jobs_dir / job_id / "job.json").read_text(encoding="utf-8"))
        return (j.get("name") or job_id)[:40], float(j.get("source", {}).get("duration") or 0)
    except (OSError, ValueError):
        return job_id, 0.0


def report(jobs_dir: Path, days: int = 7, now: datetime | None = None) -> str:
    now = now or datetime.now()
    cur_from, prev_from = now - timedelta(days=days), now - timedelta(days=2 * days)
    rows = _rows(jobs_dir / "_uso" / "uso.csv")
    cur = [r for r in rows if r["_t"] >= cur_from]
    prev = [r for r in rows if prev_from <= r["_t"] < cur_from]
    out = [f"# Uso do Claude no Cortaí: últimos {days} dias (até {now:%d/%m %H:%M})", ""]
    if not cur:
        out.append(f"Nenhuma chamada ao Claude no período ({len(rows)} no histórico).")
        return "\n".join(out)

    def line(rs: list[dict]) -> str:
        tin, cached = sum(r["_in"] for r in rs), sum(_f(r.get("cache_lido")) for r in rs)
        return (f"{len(rs)} chamadas · entrada {_k(tin)} tokens ({cached / tin:.0%} do cache) · saída "
                f"{_k(sum(r['_out'] for r in rs))} · US$ {sum(r['_usd'] for r in rs):.2f}") if tin else f"{len(rs)} chamadas"

    out += [f"**Agora:** {line(cur)}", f"**Período anterior:** {line(prev) if prev else 'sem chamadas'}", ""]
    now_s, prev_s = _by_step(cur), _by_step(prev)
    out += ["| etapa | chamadas | entrada/chamada | saída/chamada | US$/chamada | US$ total | antes (entrada/chamada) |",
            "|---|---|---|---|---|---|---|"]
    alerts = []
    for s, v in sorted(now_s.items(), key=lambda kv: kv[1]["total"], reverse=True):
        p = prev_s.get(s)
        out.append(f"| {s} | {v['n']} | {_k(v['in'])} | {_k(v['out'])} | {v['usd']:.4f} | {v['total']:.2f} | "
                   f"{_k(p['in']) if p else '—'} |")
        if p and p["in"] and v["in"] > p["in"] * (1 + ALERTA_SUBIDA):
            alerts.append(f"`{s}`: entrada por chamada subiu {v['in'] / p['in'] - 1:.0%} ({_k(p['in'])} → {_k(v['in'])}).")
        ref = REFERENCIAS.get(s)
        if ref and v["usd"] > ref * ALERTA_REFERENCIA:
            alerts.append(f"`{s}`: US$ {v['usd']:.4f}/chamada, {v['usd'] / ref:.1f}x a medição de referência ({ref}).")
        if s == "placar_ler" and any("haiku" not in m for m in v["modelos"]):
            alerts.append("`placar_ler` usou o modelo principal em vez do rápido (calibração caiu no modo de segurança).")
    for r in cur:
        if r["_in"] > ALERTA_CHAMADA:
            alerts.append(f"Chamada grande: `{r.get('etapa')}` em {r['_t']:%d/%m %H:%M} com {_k(r['_in'])} tokens de entrada.")
    per_job: dict[str, list[dict]] = {}
    for r in cur:
        per_job.setdefault(r.get("job") or "", []).append(r)
    out += ["", "**Por projeto:**"]
    for jid, rs in sorted(per_job.items(), key=lambda kv: -sum(r["_usd"] for r in kv[1])):
        usd = sum(r["_usd"] for r in rs)
        if not jid:
            out.append(f"- (fora de projeto: estilos, pós-edição ou registro antigo): {len(rs)} chamadas, US$ {usd:.2f}")
            continue
        name, dur = _job_info(jobs_dir, jid)
        rate = f", US$ {usd / (dur / 3600):.2f} por hora de vídeo" if dur > 60 else ""
        out.append(f"- {name} ({dur / 60:.0f} min): {len(rs)} chamadas, US$ {usd:.2f}{rate}")
    out += ["", "**Alertas:**", *(f"- {a}" for a in alerts[:12])] if alerts else ["", "**Alertas:** nenhum."]
    return "\n".join(out)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    text = report(paths.JOBS_DIR, days)
    dest = paths.JOBS_DIR / "_uso" / f"relatorio-{time.strftime('%Y-%m-%d')}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\n(salvo em {dest})")


if __name__ == "__main__":
    main()
