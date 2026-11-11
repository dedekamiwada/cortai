"""Títulos com potencial viral: regras para o Claude + validação, escolha e aprendizado no código.

Base: skill `titulos-virais-clips` (cerca de 400 Shorts de jogos em 5 idiomas, nov/2026, mais estudos
de títulos). Nos títulos com milhões de views o padrão é: curto, a situação específica, ironia ou
desfecho em aberto. Nos que ficam em dezenas de views: o título só diz que é um "clip" e enche de hashtag.

Fluxo:
1. `guide()` entra no prompt do Claude (candidatos e revisão visual) e pede 5 opções de fórmulas
   diferentes em `title_options`.
2. `choose()` descarta as opções que quebram as regras duras e fica com a de maior nota.
3. `record()` grava cada título em `jobs/_titulos/titulos.csv`. Você preenche as views depois de postar,
   e `guide()` passa a dizer ao Claude quais fórmulas rendem mais no seu canal.
"""
from __future__ import annotations

import csv
import re
import statistics
import threading
import unicodedata
from pathlib import Path

FORMULAS = {
    "F1": "eufemismo irônico",
    "F2": "reação/fala curta",
    "F3": "situação sem desfecho (...)",
    "F4": "identificação com tipo de jogador",
    "F5": "gatilho de compartilhamento",
    "F6": "número/detalhe específico",
    "F7": "superlativo com escopo",
    "F8": "culpa externa",
    "F9": "universal (todo X é assim)",
    "F10": "ranking (só compilação)",
}
MANUAL = "manual"

IDEAL_CHARS = 40
MAX_CHARS = 60

TITLE_OPTIONS_FIELD = {
    "type": "array",
    "description": "5 opções de título, cada uma de uma fórmula diferente, seguindo as regras de título",
    "items": {
        "type": "object", "additionalProperties": False, "required": ["title", "formula", "score"],
        "properties": {
            "title": {"type": "string"},
            "formula": {"type": "string", "enum": list(FORMULAS)},
            "score": {"type": "integer", "description": "0 a 10"},
        },
    },
}

RULES = """Regras de título (valem mais que o guia de estilo; só as instruções extras do usuário passam por cima):
O título vai no Shorts/TikTok/Reels e pode aparecer escrito no topo do vídeo. Quem vê não conhece o streamer: \
o título tem que funcionar sem saber quem ele é e fazer a pessoa parar de rolar.

Fórmulas (exemplos reais de Shorts com milhões de views entre parênteses):
F1 eufemismo irônico: trata o desastre como pouca coisa ou como mérito ("Slight Miscalculation"; "excelente hora pra ler o chat")
F2 reação/fala curta, 1 a 5 palavras, só se fizer sentido sem contexto ("THIS GOT ME"; "eu não queria")
F3 situação sem desfecho, terminando em "..." ("When You Go for the Jump in Lethal Company..."; "parei 2 segundos pra ler o chat...")
F4 identificação com um tipo de jogador ("xQc is your Average Ranked Teammate"; "todo squad tem um...")
F5 gatilho de compartilhamento ("já manda esse vídeo pro seu amigo Platina")
F6 número ou detalhe específico ("1 bala, 5 abates na Inferno"; "170 pessoas na live e...")
F7 superlativo com escopo ("The sneakiest round win in Immortal"; "a fuga mais burra que você vai ver hoje")
F8 culpa externa ("o chat me matou"; "o jogo ouviu")
F9 universal ("Every Round of Peak Ever"; "toda partida de Devour é assim")
F10 ranking: só para compilação

Regras duras:
- de preferência até 40 caracteres; nunca mais de 60;
- nunca nomeie a categoria (clipe, corte, gameplay, highlights, melhores momentos, funny moments);
- sem hashtag, sem "kkkk", sem "!!!", no máximo 1 emoji e só no fim;
- use o detalhe mais específico do trecho (número, objeto, o que o chat disse, rank, o que foi perdido) \
em vez de adjetivo (insano, épico, engraçado);
- não entregue a punchline visual se o corte depende da surpresa. Exceção: ironia dramática, quando o público \
sabe o que vem e assiste para ver ("falou que o jogo não dava susto...");
- afirme em vez de perguntar; nome do jogo só quando couber natural; nada de promessa que o corte não cumpre;
- só citar a frase dita ("DESGRAÇA, VELHO") funciona para streamer famoso. Para streamer pequeno, prefira dar o \
contexto ou a ironia, e use a fala citada no máximo como uma das opções;
- escreva em caixa normal: o estilo do vídeo aplica caixa alta quando precisar.

"""

OPTIONS_RULE = """Em `title_options` dê 5 opções de fórmulas diferentes (as que servem para o tipo de clipe), cada uma \
com nota 0 a 10: 2 pontos para cada um de curto, específico, quebra de expectativa ou desfecho em aberto, sem \
spoiler indevido, alguém mandaria para um amigo. Ponha a melhor também em `title`. Pense no título junto com o \
`post`: o post amplia a curiosidade e não repete o título."""

DRAFT_RULE = """Nesta etapa o `title` é um rascunho que já segue as regras; as opções finais vêm na revisão visual."""

_CATEGORY = re.compile(
    r"\b(clipe?s?|cortes?(\s+da\s+live)?|gameplay|highlights?|melhores\s+momentos|funny\s+moments?|"
    r"momentos?\s+(?:mais\s+)?engra[cç]ad[oa]s?)\b", re.I)
_LAUGH = re.compile(r"k{3,}|(?:ha){3,}|(?:rs){2,}|(?:ja){3,}", re.I)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿]")
_HASHTAG = re.compile(r"#\w+")

_lock = threading.Lock()
FIELDS = ["job", "n", "titulo", "formula", "caracteres", "views", "visualizado_pct", "observacao"]


# ------------------------------------------------------------------ títulos novos para clipes prontos

RETITLE_SYSTEM = """Você é o editor de títulos de um canal de cortes de lives de jogos. Os clipes abaixo já estão \
cortados e revisados: para cada um você recebe o que se vê na tela, o que prende no começo, por que funciona, \
a fala do trecho e o título atual. Escreva títulos novos seguindo as regras de título, e um `post` novo: \
descrição curta no idioma do vídeo, tom de zoeira, uma linha que amplia a curiosidade sem repetir o título \
(sem "kkkk"), terminando com 3 a 5 hashtags (jogo + nicho). Mantenha as hashtags fixas do canal que aparecerem \
no post atual (ex.: o @ ou nome do streamer)."""

RETITLE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["clips"],
    "properties": {"clips": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["n", "title_options", "title", "post"],
        "properties": {
            "n": {"type": "integer", "description": "Número do clipe"},
            "title_options": TITLE_OPTIONS_FIELD,
            "title": {"type": "string"},
            "post": {"type": "string"},
        },
    }}},
}


def retitle_prompt(clips: list[dict], speech: dict[int, str]) -> str:
    parts = []
    for c in clips:
        parts.append(f"## Clipe {c['n']} ({c['end'] - c['start']:.0f}s)")
        for label, key in (("Título atual", "title"), ("Na tela", "visual"), ("Gancho", "hook"),
                           ("Por que funciona", "reason"), ("Post atual", "post")):
            if c.get(key):
                parts.append(f"{label}: {c[key]}")
        parts.append(f"Fala do trecho: {speech.get(c['n']) or '(sem fala)'}")
        parts.append("")
    parts.append('Responda somente com JSON {"clips": [...]} com um item por clipe, mantendo o número em `n`.')
    return "\n".join(parts)


# ------------------------------------------------------------------ validação e escolha

def problems(title: str, compilation: bool = False) -> list[str]:
    """Regras duras que dá para checar no código. Lista vazia = título aceitável."""
    t = (title or "").strip()
    if not t:
        return ["vazio"]
    out = []
    if len(t) > MAX_CHARS:
        out.append(f"{len(t)} caracteres (máx. {MAX_CHARS})")
    if _HASHTAG.search(t):
        out.append("hashtag no título")
    if _LAUGH.search(t):
        out.append("risada escrita")
    if "!!" in t or "??" in t:
        out.append("pontuação repetida")
    emojis = _EMOJI.findall(t)
    if len(emojis) > 1:
        out.append("mais de 1 emoji")
    elif emojis and not _EMOJI.search(t[-3:]):
        out.append("emoji fora do fim")
    if not compilation and _CATEGORY.search(t):
        out.append("nomeia a categoria")
    return out


def clean(title: str) -> str:
    """Conserto mínimo para quando nenhuma opção passa: tira hashtags e risadas e corta no limite."""
    t = _HASHTAG.sub("", title or "")
    t = _LAUGH.sub("", t)
    t = re.sub(r"([!?])\1+", r"\1", t)
    emojis = _EMOJI.findall(t)
    if len(emojis) > 1:
        t = _EMOJI.sub("", t).rstrip() + " " + emojis[0]
    t = re.sub(r"\s+", " ", t).strip(" -|·")
    if len(t) > MAX_CHARS:
        cut = t[:MAX_CHARS].rsplit(" ", 1)[0]
        t = cut.rstrip(",;:-") + "..."
    return t


def choose(options: list[dict] | None, fallback: str) -> dict:
    """Fica com a opção válida de maior nota (no empate, a mais curta). Devolve título, fórmula e opções anotadas."""
    annotated = []
    for o in options or []:
        title = str(o.get("title", "")).strip()
        if not title:
            continue
        formula = o.get("formula") if o.get("formula") in FORMULAS else ""
        annotated.append({
            "title": title, "formula": formula,
            "score": max(0, min(10, int(o.get("score") or 0))),
            "problems": problems(title, compilation=formula == "F10"),
        })
    valid = [o for o in annotated if not o["problems"]]
    if valid:
        best = max(valid, key=lambda o: (o["score"], -max(0, len(o["title"]) - IDEAL_CHARS)))
        return {"title": best["title"], "title_formula": best["formula"], "title_options": annotated}
    # nenhuma opção válida (ou resposta antiga sem opções): conserta o título que veio
    base = (fallback or "").strip() or (annotated[0]["title"] if annotated else "")
    match = next((o for o in annotated if o["title"] == base), None)
    return {"title": clean(base), "title_formula": match["formula"] if match else "", "title_options": annotated}


def formula_of(clip: dict, title: str) -> str:
    """Fórmula de um título escolhido no editor: a da opção igual, senão `manual`."""
    norm = _norm(title)
    for o in clip.get("title_options") or []:
        if _norm(o.get("title", "")) == norm:
            return o.get("formula") or MANUAL
    return MANUAL


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


# ------------------------------------------------------------------ aprendizado com os resultados reais

def log_path(root: Path) -> Path:
    d = root / "_titulos"
    d.mkdir(exist_ok=True)
    return d / "titulos.csv"


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def record(root: Path, job_id: str, clips: list[dict]) -> None:
    """Grava/atualiza os títulos no CSV, mantendo as views que você já preencheu.

    O CSV usa `;` e BOM para abrir direto no Excel em português.
    """
    path = log_path(root)
    with _lock:
        try:
            rows = _read(path)
        except Exception:
            return  # CSV aberto/corrompido: aprender é opcional, não derruba o job
        index = {(r.get("job"), r.get("n")): r for r in rows}
        for c in clips:
            key = (job_id, str(c["n"]))
            row = index.get(key)
            if row is None:
                row = {k: "" for k in FIELDS}
                row.update(job=job_id, n=str(c["n"]))
                rows.append(row)
                index[key] = row
            if row.get("titulo") != c["title"]:
                row.update(titulo=c["title"], formula=c.get("title_formula") or "",
                           caracteres=str(len(c["title"])))
        tmp = path.with_suffix(".tmp")
        try:
            with tmp.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS, delimiter=";", extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            tmp.replace(path)
        except PermissionError:
            tmp.unlink(missing_ok=True)  # Excel com o arquivo aberto: tenta de novo no próximo job


def _number(text: str) -> float | None:
    """Aceita 12000, 12.000, 12,5 mil, 12k, 1,2M, 1.2 mi."""
    t = (text or "").strip().lower().replace(" ", "")
    if not t:
        return None
    mult = 1
    for suffix, m in (("mil", 1e3), ("mi", 1e6), ("k", 1e3), ("m", 1e6)):
        if t.endswith(suffix):
            t, mult = t[: -len(suffix)], m
            break
    if mult == 1:
        t = t.replace(".", "").replace(",", "")
    else:
        t = t.replace(",", ".")
    try:
        return float(t) * mult
    except ValueError:
        return None


def performance(root: Path, min_posts: int = 3) -> list[tuple[str, float, int]]:
    """Mediana de views por fórmula, só das fórmulas com pelo menos `min_posts` posts medidos."""
    try:
        rows = _read(log_path(root))
    except Exception:
        return []
    by: dict[str, list[float]] = {}
    for r in rows:
        views = _number(r.get("views", ""))
        if views is not None and r.get("formula"):
            by.setdefault(r["formula"], []).append(views)
    stats = [(f, statistics.median(v), len(v)) for f, v in by.items() if len(v) >= min_posts]
    return sorted(stats, key=lambda s: s[1], reverse=True)


def guide(root: Path | None = None, options: bool = True) -> str:
    """Regras de título para o prompt. `options=False` na etapa de candidatos (o schema não tem `title_options`)."""
    text = "\n\n" + RULES.rstrip() + "\n\n" + (OPTIONS_RULE if options else DRAFT_RULE)
    stats = performance(root) if root else []
    if len(stats) >= 2:
        lines = [f"- {f} ({FORMULAS.get(f, f)}): mediana de {v:,.0f} views em {n} posts".replace(",", ".")
                 for f, v, n in stats]
        text += ("\n\nResultados reais deste canal (das fórmulas medidas, da melhor para a pior):\n"
                 + "\n".join(lines)
                 + "\nDê preferência às fórmulas de cima quando servirem para o clipe, mas mantenha pelo menos uma "
                   "opção de fórmula menos testada para o aprendizado continuar.")
    return text
