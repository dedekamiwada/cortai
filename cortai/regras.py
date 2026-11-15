"""Pedidos simples de tempo resolvidos por código, sem gastar tokens: "começa 3 s antes", "termina 2 s depois",
"tira os primeiros 5 s", "corta 1,5 s do fim", "estende 4 s no final". Qualquer pedaço que não for um desses
devolve None e o pedido inteiro vai para o Claude (que vê os quadros e a fala)."""
from __future__ import annotations

import re
import unicodedata

_NUM_WORDS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
              "oito": 8, "nove": 9, "dez": 10, "meio": 0.5}
_NUM = r"(\d+(?:[.,]\d+)?|" + "|".join(_NUM_WORDS) + r")"
_SEC = r"\s*(?:s|seg|segs|segundo|segundos)?"
_START = r"(?:comec\w*|inici\w*|abr\w*)"
_END = r"(?:termin\w*|acab\w*|fim|final|fecha\w*)"
# cada regra: (padrão, borda, sinal) — sinal +1 empurra a borda para depois, -1 para antes
_RULES = [
    (rf"^{_START}\s+(?:o corte\s+)?{_NUM}{_SEC}\s+(?:antes|mais cedo)$", "s", -1),
    (rf"^{_START}\s+(?:o corte\s+)?{_NUM}{_SEC}\s+(?:depois|mais tarde|adiante)$", "s", +1),
    (rf"^{_END}\s+(?:o corte\s+)?{_NUM}{_SEC}\s+(?:antes|mais cedo)$", "e", -1),
    (rf"^{_END}\s+(?:o corte\s+)?{_NUM}{_SEC}\s+(?:depois|mais tarde|adiante)$", "e", +1),
    (rf"^(?:tir\w*|cort\w*|remov\w*)\s+(?:os\s+|as\s+)?(?:primeiros?|primeiras?)\s+{_NUM}{_SEC}$", "s", +1),
    (rf"^(?:tir\w*|cort\w*|remov\w*)\s+(?:os\s+|as\s+)?(?:ultimos?|ultimas?)\s+{_NUM}{_SEC}$", "e", -1),
    (rf"^(?:tir\w*|cort\w*|remov\w*)\s+{_NUM}{_SEC}\s+(?:do|no)\s+(?:comeco|inicio)$", "s", +1),
    (rf"^(?:tir\w*|cort\w*|remov\w*)\s+{_NUM}{_SEC}\s+(?:do|no)\s+(?:fim|final)$", "e", -1),
    (rf"^(?:estend\w*|aument\w*|alarg\w*|adicion\w*|poe|bota)\s+(?:mais\s+)?{_NUM}{_SEC}\s+(?:no|do|ao)\s+(?:comeco|inicio)$", "s", -1),
    (rf"^(?:estend\w*|aument\w*|alarg\w*|adicion\w*|poe|bota)\s+(?:mais\s+)?{_NUM}{_SEC}\s+(?:no|do|ao)\s+(?:fim|final)$", "e", +1),
]


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _num(tok: str) -> float:
    return float(_NUM_WORDS.get(tok, None) or tok.replace(",", "."))


def time_edit(instruction: str, start: float, end: float) -> tuple[float, float] | None:
    """Novo (início, fim) se TODO o pedido for ajuste de borda; senão None (vai para o Claude)."""
    text = _fold(instruction).strip().rstrip(".!")
    clauses = [c.strip() for c in re.split(r"\s*(?:(?<!\d),|,(?!\d)|;| e (?=\D)| e$)\s*", text) if c.strip()]
    if not clauses:
        return None
    s, e = start, end
    for clause in clauses:
        clause = re.sub(r"^(?:e\s+|que\s+|pra\s+|para\s+)", "", clause)
        for pattern, edge, sign in _RULES:
            m = re.match(pattern, clause)
            if m:
                delta = sign * _num(m.group(1))
                if edge == "s":
                    s += delta
                else:
                    e += delta
                break
        else:
            return None
    return (s, e) if e - s >= 1 else None
