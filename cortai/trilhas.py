"""Músicas que dão emoção a cada tipo de jogada (indicação para a pós-edição; o Cortaí não traz os arquivos).

A pós-edição já decide o clima de cada trecho da trilha (tensão antes do lance, épico na jogada boa, cômico na
falha, triste na derrota). Aqui fica, por clima, o que costuma funcionar em edits de jogo e memes — escolhido
por código, sem tokens. Em edits de jogo de alta energia o phonk domina (pesquisa nov/2026); os clássicos de
meme cobrem falha e derrota. "livre" = Kevin MacLeod (incompetech), CC BY 4.0: use com crédito, sem reivindicação.
"comercial" = no TikTok/Reels use o som do próprio app; no YouTube o Content ID costuma reivindicar.
"""
from __future__ import annotations

import re
import unicodedata

CATALOG = {
    "epico": [
        {"titulo": "Metamorphosis", "artista": "INTERWORLD", "momento": "jogada absurda, gol decisivo",
         "como": "phonk; entre com a batida pesada (o drop) exatamente no lance", "licenca": "comercial"},
        {"titulo": "Murder In My Mind", "artista": "Kordhell", "momento": "sequência de jogadas, aerial, virada",
         "como": "phonk; corte as jogadas no ritmo do cowbell", "licenca": "comercial"},
        {"titulo": "Sahara", "artista": "Hensonn", "momento": "gol no último segundo, prorrogação vencida",
         "como": "phonk; segure o lance em câmera lenta e solte no drop", "licenca": "comercial"},
        {"titulo": "Eye of the Tiger", "artista": "Survivor", "momento": "virada, superação depois de perder",
         "como": "entre no riff quando o placar empata", "licenca": "comercial"},
        {"titulo": "We Are The Champions", "artista": "Queen", "momento": "vitória da série, rank novo",
         "como": "refrão na tela de vitória, em tom de exagero", "licenca": "comercial"},
    ],
    "comico": [
        {"titulo": "Oh No", "artista": "Capone", "momento": "falha óbvia que dá para ver chegando",
         "como": "o \"oh no, no, no\" começa um pouco ANTES do erro", "licenca": "comercial"},
        {"titulo": "Yakety Sax", "artista": "Boots Randolph", "momento": "caos, todo mundo errando a bola",
         "como": "acelere o trecho junto (1,5×) para virar pastelão", "licenca": "comercial"},
        {"titulo": "Roundabout (\"To be continued\")", "artista": "Yes", "momento": "congela antes do desastre",
         "como": "a guitarra entra, congela o quadro e escreve \"to be continued\"", "licenca": "comercial"},
        {"titulo": "Monkeys Spinning Monkeys", "artista": "Kevin MacLeod", "momento": "situação boba, desculpa esfarrapada",
         "como": "fundo leve durante a explicação", "licenca": "livre"},
        {"titulo": "Fluffing a Duck", "artista": "Kevin MacLeod", "momento": "erro bobo, bola que não entra",
         "como": "fundo leve; corte seco no erro", "licenca": "livre"},
        {"titulo": "Sneaky Snitch", "artista": "Kevin MacLeod", "momento": "jogada \"esperta\" que dá errado",
         "como": "entra na preparação, para no erro", "licenca": "livre"},
    ],
    "triste": [
        {"titulo": "Mad World", "artista": "Gary Jules", "momento": "derrota, gol sofrido no fim",
         "como": "câmera lenta no replay do gol sofrido", "licenca": "comercial"},
        {"titulo": "Sadness and Sorrow", "artista": "Toshio Masuda (Naruto)", "momento": "pena exagerada, rank perdido",
         "como": "flauta entrando logo depois da derrota", "licenca": "comercial"},
    ],
    "tensao": [
        {"titulo": "Lux Aeterna", "artista": "Clint Mansell", "momento": "último minuto, placar empatado",
         "como": "crescendo até o lance; corte seco no gol", "licenca": "comercial"},
        {"titulo": "Duel of the Fates", "artista": "John Williams", "momento": "prorrogação, gol de ouro",
         "como": "coro entrando no kickoff da prorrogação", "licenca": "comercial"},
        {"titulo": "Volatile Reaction", "artista": "Kevin MacLeod", "momento": "pressão, defesa atrás de defesa",
         "como": "fundo do lance inteiro, sem precisar de corte", "licenca": "livre"},
    ],
}


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return re.sub(r"[^a-z0-9]+", "-", "".join(c for c in text if unicodedata.category(c) != "Mn")).strip("-")


def suggest(climas: list[str], library_music: dict[str, list[str]]) -> dict[str, list[dict]]:
    """Indicações por clima usado no plano. Marca as que já estão na biblioteca (nome do arquivo contém o título)."""
    out = {}
    for clima in dict.fromkeys(climas):
        files = [_slug(f) for f in library_music.get(clima, [])]
        items = []
        for s in CATALOG.get(clima, []):
            slug = _slug(s["titulo"].split(" (")[0])
            items.append({**s, "clima": clima, "arquivo_sugerido": f"musicas/{clima}/{slug}.mp3",
                          "na_biblioteca": any(slug in f for f in files), "buscar": f"{s['titulo']} {s['artista']}"})
        if items:
            out[clima] = items
    return out
