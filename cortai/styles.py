"""Estilos de corte: guias que dizem ao Claude *como* cortar.

Um estilo nasce de duas formas: o embutido (destilado de cortes de streamers BR de
sucesso) ou aprendido — você cola links de cortes que admira, o app baixa, transcreve,
extrai frames e o Claude escreve o guia a partir do que viu e ouviu.
"""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path

BUILTIN = {
    "id": "streamer-br",
    "name": "Cortes de streamer BR (estilo alanzoka)",
    "builtin": True,
    "status": "ready",
    "sources": [
        "youtube.com/shorts/tjaSgDaLpbo", "youtube.com/shorts/p50WVAv6lZU",
        "youtube.com/shorts/Zri7BuOYeL0", "youtube.com/shorts/cYdbXwOc0og",
    ],
    "guide": """\
Padrões observados em cortes de canais de cortes de streamers brasileiros com centenas de milhares de views:

- UM acontecimento por corte, 15 a 35 segundos. Nada de compilado dentro do corte.
- O acontecimento é quase sempre VISUAL ou uma REAÇÃO, não uma frase: o jogo trava/reinicia, o personagem morre de
  forma idiota, um susto, um bug, um erro bizarro — e a cara do streamer. Na transcrição isso aparece como
  intervalo sem fala, risada, grito ou som alto do jogo. Ex.: num corte de sucesso o melhor momento são 7,5 s sem
  nenhuma palavra (o jogo travou e ele só ri).
- Começo seco, direto na fala/ação que monta a situação: 5 a 10 s de contexto antes do acontecimento (nos
  exemplos o jogo trava aos ~7 s e a punchline cai aos ~10 s). Nunca começar com cumprimento ou "então galera".
- Deixa a reação respirar: depois do evento mantém risada, silêncio constrangido, cara de choque; termina quando a
  reação acaba ou numa frase de efeito curta. Não corta no meio da risada.
- Piadas de bate-papo também funcionam, mas só se a punchline for curta e a reação aparecer (a pausa/cara depois
  da piada é parte do corte).
- Título: tom de zoeira, curto e sem clickbait explicativo, seguindo as regras de título. Nesses canais de cortes
  o título costuma citar a frase marcante ("AIN PRENDI MEU DEDINHO"), mas isso se apoia na fama do streamer:
  para canal pequeno, citar a fala é só uma das opções.
""",
}


def _dir(root: Path) -> Path:
    d = root / "_styles"
    d.mkdir(exist_ok=True)
    return d


def list_styles(root: Path) -> list[dict]:
    out = [BUILTIN]
    for f in sorted(_dir(root).glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def get(root: Path, style_id: str | None) -> dict | None:
    if not style_id:
        return None
    return next((s for s in list_styles(root) if s["id"] == style_id), None)


def save(root: Path, style: dict) -> None:
    f = _dir(root) / f"{style['id']}.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(style, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)


def delete(root: Path, style_id: str) -> None:
    if style_id == BUILTIN["id"]:
        raise ValueError("o estilo embutido não pode ser excluído")
    (_dir(root) / f"{style_id}.json").unlink(missing_ok=True)
    shutil.rmtree(_dir(root) / style_id, ignore_errors=True)


def new(root: Path, name: str, urls: list[str], notes: str) -> dict:
    urls = [u.strip() for u in urls if re.match(r"^https?://", u.strip())]
    if not urls:
        raise ValueError("cole pelo menos um link de vídeo")
    if len(urls) > 6:
        raise ValueError("use no máximo 6 vídeos de referência")
    style = {
        "id": "st-" + uuid.uuid4().hex[:8], "name": name.strip() or "Meu estilo",
        "builtin": False, "status": "learning", "message": "Na fila", "error": None,
        "sources": urls, "notes": notes.strip(), "guide": "", "created": time.time(),
    }
    save(root, style)
    return style


LEARN_SYSTEM = """Você é um editor que estuda cortes de vídeo de sucesso para escrever um guia de estilo que \
outro editor (também uma IA) vai seguir ao cortar lives longas.

Para cada vídeo de referência você recebe a transcrição com marcadores de áudio (gritos, risadas, silêncios, som \
do jogo) e uma imagem com quadros do vídeo marcados com o segundo. Observe:
- quanto tempo de contexto vem antes do acontecimento principal e onde ele cai;
- se o acontecimento é visual (jogo, câmera), uma reação ou uma fala;
- como o corte termina;
- duração, enquadramento/layout, legendas, textos na tela, zoom;
- o estilo dos títulos.

Escreva um guia prático em português, em tópicos curtos, com regras acionáveis (números quando possível) e \
exemplos concretos do que você viu. Não descreva cada vídeo: generalize o padrão. Máximo ~250 palavras."""


def learn_prompt(refs: list[dict], notes: str) -> str:
    parts = []
    for i, ref in enumerate(refs, 1):
        parts.append(f"## Referência {i}: {ref['title']} ({ref['duration']:.0f}s, imagem {ref['image_name']})")
        parts.append(ref["transcript"] or "(sem fala)")
    if notes:
        parts.append(f"\nObservações do usuário sobre o que ele gosta nesses cortes: {notes}")
    parts.append('\nResponda com JSON {"guide": "<o guia>", "summary": "<uma frase>"}.')
    return "\n".join(parts)


LEARN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["guide", "summary"],
    "properties": {"guide": {"type": "string"}, "summary": {"type": "string"}},
}
