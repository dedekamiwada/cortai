"""Catálogo de sons de meme da biblioteca: o que é cada um, quando usar e o risco de direitos.

O Cortaí não traz esses áudios (são de terceiros). Cada item diz o nome de arquivo esperado; quando o arquivo
existe na biblioteca, a pós-edição recebe o "quando usar" junto com o nome, e escolhe o som pelo momento do
clipe, não só pelo nome do arquivo. Os arquivos entram pela pasta ou pelo "Importar por link" (você escolhe a
fonte e o trecho). Pesquisa de origem: Know Your Meme e afins, nov/2026.
"""
from __future__ import annotations

# direitos: "baixo" = trecho curto de clipe viral, costuma passar; "alto" = música comercial ou de desenho,
# o YouTube costuma reivindicar pelo Content ID (no TikTok, prefira o som do próprio app)
CATALOG = [
    {"id": "fahh", "nome": "Fahh / Faaah", "arquivo": "sfx/meme-fahh.mp3", "pasta": "sfx",
     "uso": "falha, erro bobo, gol perdido, susto ou decepção: logo depois do erro, como punchline",
     "origem": "grito do streamer Taileons (2024), virou som de compilação de falhas",
     "direitos": "baixo", "buscar": "fahh sound effect"},
    {"id": "vine-boom", "nome": "Vine boom", "arquivo": "sfx/meme-vine-boom.mp3", "pasta": "sfx",
     "uso": "revelação, olhar para a câmera, frase absurda ou momento \"o quê?\"",
     "origem": "efeito do Vine (2014), o som de reação mais usado em memes",
     "direitos": "baixo", "buscar": "vine boom sound effect"},
    {"id": "tipo-ah", "nome": "É tipo, é tipo ah", "arquivo": "sfx/meme-tipo-ah.mp3", "pasta": "sfx",
     "uso": "quando o streamer não sabe explicar a jogada ou a desculpa",
     "origem": "meme brasileiro do TikTok (origem não documentada nas fontes pesquisadas: importe pelo link do vídeo)",
     "direitos": "baixo", "buscar": "é tipo é tipo ah"},
    {"id": "rizz", "nome": "Rizz song (Me U & Hennessy)", "arquivo": "musicas/comico/meme-rizz.mp3", "pasta": "musicas/comico",
     "uso": "jogada estilosa, flerte, \"rizz\" no jogo; trecho curto com o streamer sorrindo para a câmera",
     "origem": "DeJ Loaf com Lil Wayne; virou o som do \"rizz chin-up\" no TikTok",
     "direitos": "alto", "buscar": "rizz song me u and hennessy"},
    {"id": "bob-grass-skirt", "nome": "Bob Esponja — Grass Skirt Chase", "arquivo": "musicas/comico/bob-grass-skirt-chase.mp3",
     "pasta": "musicas/comico", "uso": "correria, perseguição, caos cômico (todo mundo atrás da bola)",
     "origem": "trilha de produção usada no Bob Esponja", "direitos": "alto", "buscar": "grass skirt chase spongebob"},
    {"id": "bob-hawaiian", "nome": "Bob Esponja — Hawaiian Cocktail", "arquivo": "musicas/comico/bob-hawaiian-cocktail.mp3",
     "pasta": "musicas/comico", "uso": "calmaria irônica depois do desastre, \"tudo sob controle\"",
     "origem": "trilha de produção usada no Bob Esponja", "direitos": "alto", "buscar": "hawaiian cocktail spongebob"},
    {"id": "bob-horas-depois", "nome": "Bob Esponja — \"Algumas horas depois\"", "arquivo": "sfx/bob-horas-depois.mp3",
     "pasta": "sfx", "uso": "pulo no tempo: fila demorada, partida que não acaba, espera",
     "origem": "cartão do narrador francês do Bob Esponja", "direitos": "alto", "buscar": "spongebob few moments later"},
    {"id": "bob-triste", "nome": "Bob Esponja — violino triste", "arquivo": "musicas/triste/bob-violino-triste.mp3",
     "pasta": "musicas/triste", "uso": "derrota, gol sofrido no fim, momento de pena exagerada",
     "origem": "trilha triste do Bob Esponja usada em memes de derrota", "direitos": "alto",
     "buscar": "spongebob sad violin"},
]
FOLDERS = ("sfx", "musicas/comico", "musicas/epico", "musicas/tensao", "musicas/triste")


def hints(present: set[str]) -> dict[str, str]:
    """{nome do arquivo: quando usar} só dos memes que existem na biblioteca."""
    return {m["arquivo"].split("/")[-1]: m["uso"] for m in CATALOG if m["arquivo"] in present}
