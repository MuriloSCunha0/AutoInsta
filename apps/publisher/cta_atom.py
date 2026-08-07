# -*- coding: utf-8 -*-
"""Banco de CTAs do plano ATOM — legendas de reel e arte de story.

Por que um banco e não um texto só: o Instagram remove a legenda (e derruba o
alcance) de posts que parecem coordenados, e 18 contas publicando a MESMA frase
no mesmo minuto é o padrão mais fácil de detectar que existe. Ver
[[legenda-caption-conta-confianca]] e [[reach-zero-conta-nova-volume]].

Duas camadas de variação, que se multiplicam:

 1. **Rotação de modelo** (aqui): o post i de cada conta pega um modelo
    diferente, com um deslocamento por conta — duas contas nunca saem com o
    mesmo modelo no mesmo ciclo, e a mesma conta não repete modelo antes de dar
    a volta no banco inteiro.
 2. **Spintax `{a|b|c}`** (em `caption_utils.variar_legenda`, já existente):
    resolvido de forma determinística por conta+post. Cada modelo abaixo tem
    dezenas de combinações, então na prática nenhuma legenda se repete.

Todo CTA aponta para o LINK NOS DESTAQUES — não para "link na bio" — porque o
destaque é o que sobrevive às 24h do story (ver `fixar_no_destaque` na engine).
"""

# ── Legendas de reel/feed ────────────────────────────────────────────────────
# Cada uma é um MODELO em spintax. Mantidas curtas: legenda longa com link e
# apelo repetido é o que mais chama moderação.
LEGENDAS = [
    "{oi|oie|ei} 🥰 {o link tá|deixei o link|tá tudo} nos {destaques|meus destaques} 👆",
    "{passa|corre|vem} nos {destaques|destaques do perfil} pra ver o {resto|restante} 🔥",
    "{tem mais|tem bem mais|tem muito mais} no {destaque|destaque do perfil} ✨",
    "{clica|toca|vai} no {destaque|primeiro destaque} do perfil 💋",
    "{a continuação|o resto|o restinho} tá {nos destaques|no destaque} 😌",
    "{não perde|não fica de fora|aproveita} — {link|tá} nos {destaques|destaques} 💫",
    "{quem viu|quem chegou} {sabe|já sabe} 😏 {destaques|nos destaques} 👀",
    "{deixei|guardei} {um presentinho|uma surpresa} nos {destaques|destaques} 🎁",
    "{link|acesso} {nos destaques|no destaque} pra quem {quiser ver mais|quiser mais} 💖",
    "{te espero|espero você|te aguardo} {nos destaques|lá nos destaques} 🤍",
    "{spoiler|prévia|só uma prévia} 🙈 {o resto|o completo} tá nos {destaques|destaques}",
    "{bom dia|oi|oie} {amor|lindeza|gata} ☀️ {destaques|passa nos destaques} 💕",
    "{hoje|hj} {tá|ficou} {assim|desse jeito} 🔥 {mais|tem mais} nos {destaques|destaques}",
    "{vem ver|corre ver|dá uma olhada} 👀 {destaque|no destaque} do perfil 💗",
    "{só|apenas} {pra quem|quem} {clica|entra} nos {destaques|destaques} 😇",
    "{adivinha|sabe} {onde|onde tá}? {nos destaques|no destaque} 🔗",
    "{me acha|me encontra} {nos destaques|lá nos destaques} 🦋",
    "{quer ver|quer mais}? {destaques|tá nos destaques} 💘",
    "{a melhor parte|o melhor} {ficou|tá} {nos destaques|guardado nos destaques} 🌟",
    "{oi|ei} você 👋 {olha|vê} os {destaques|meus destaques} 💞",
    "{tá|ficou} {esperando|te esperando} {nos destaques|no destaque} 🥺",
    "{sem enrolação|direto ao ponto}: {destaques|link nos destaques} ⚡",
    "{clica|toca} {ali|no} {destaque|destaque redondo} do perfil 🔥",
    "{prontinho|pronto} 💅 {o link tá|tá} nos {destaques|destaques}",
]

# ── Arte do story (gerada por engine.cta_render.gerar_cta) ───────────────────
# Título grande na imagem. Sem spintax: aqui é imagem, o texto é queimado no
# pixel — a variação vem da rotação e do frame de fundo, que muda por conta.
TITULOS_STORY = [
    "TÁ NOS DESTAQUES 👆",
    "O LINK TÁ AQUI 🔗",
    "CORRE VER 🔥",
    "SÓ CLICAR 👇",
    "NÃO PERDE ISSO 👀",
    "PRA VOCÊ 💋",
    "ABRE AQUI ✨",
    "TÔ TE ESPERANDO 🥰",
]

# Texto da pílula branca (o adesivo de link). Curto: a pílula é estreita.
BOTOES_STORY = [
    "CLIQUE AQUI",
    "VER AGORA",
    "ABRIR LINK",
    "QUERO VER",
    "ACESSAR",
    "TOCA AQUI",
]

# Título do destaque onde os stories com link são fixados.
DESTAQUE_TITULO = "LINK 🔗"


def _rodizio(banco, i, deslocamento=0):
    """Item do banco para a posição `i`, deslocado por conta.

    O deslocamento é o que impede as contas de postarem o mesmo texto no mesmo
    ciclo — cada conta começa num ponto diferente do banco.

    ATENÇÃO ao que se passa em `deslocamento`: tem de ser a POSIÇÃO da conta na
    campanha (0, 1, 2...), não o `id` dela. Com o id, duas contas cujos ids
    diferem por um múltiplo do tamanho do banco caem no MESMO modelo — foi o que
    aconteceu em produção (ids 490 e 634, banco de 24: 634-490=144=24×6), e as
    duas contas publicaram a legenda idêntica no mesmo minuto, que é exatamente
    o padrão coordenado que este módulo existe para evitar. Com a posição, duas
    contas só repetem quando a campanha tem mais contas do que modelos.
    """
    if not banco:
        return ''
    return banco[(i + deslocamento) % len(banco)]


def legenda(i, pos_conta=0):
    """Legenda (modelo spintax) do i-ésimo post da conta na posição `pos_conta`."""
    return _rodizio(LEGENDAS, i, pos_conta)


def titulo_story(i, pos_conta=0):
    return _rodizio(TITULOS_STORY, i, pos_conta)


def botao_story(i, pos_conta=0):
    return _rodizio(BOTOES_STORY, i, pos_conta)
