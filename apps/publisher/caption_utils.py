# -*- coding: utf-8 -*-
"""Variação automática de legenda por conta.

Descoberta (jul/2026): o Instagram REMOVE o texto (caption) de posts que
parecem coordenados — mesma mídia + mesma legenda publicadas em várias contas
ao mesmo tempo. Comprovado: com o MESMO app/token/código, uma conta confiável
manteve a legenda e as demais saíram sem texto. O código está correto (idêntico
a projetos que funcionam); o filtro de integridade do IG é que corta.

Solução: fazer cada conta postar uma legenda ÚNICA. Duas camadas:
  1. SPINTAX `{a|b|c}` — variação real de texto quando o usuário fornece opções.
  2. Variação INVISÍVEL — insere caracteres de largura-zero em poucas posições
     seguras (não toca em #, @ ou links) e varia as quebras finais, tornando a
     legenda byte-única por conta mesmo quando o texto é igual.

Tudo determinístico pelo `seed` (conta+post): o retry gera a MESMA legenda,
sem re-variar.
"""
import hashlib
import random
import re

# Só tratamos como spintax quando há um '|' dentro das chaves — assim não
# conflita com variáveis como {nome_conta} (que já foram resolvidas antes).
_SPINTAX = re.compile(r'\{([^{}]*\|[^{}]*)\}')


def _rng(seed):
    h = hashlib.md5((seed or '').encode('utf-8')).hexdigest()
    return random.Random(int(h[:8], 16))


def expandir_spintax(texto, rng):
    """Resolve `{a|b|c}` escolhendo uma opção (determinístico pelo rng)."""
    guarda = 0
    while guarda < 200:
        m = _SPINTAX.search(texto)
        if not m:
            break
        opcoes = [o for o in m.group(1).split('|')]
        texto = texto[:m.start()] + rng.choice(opcoes).strip() + texto[m.end():]
        guarda += 1
    return texto


# ── Variação SEMÂNTICA (algoritmo local, sem IA) ─────────────────────────────
# Objetivo: deixar cada conta com uma legenda com o MESMO SENTIDO mas texto
# diferente (o IG remove texto idêntico em massa por parecer coordenado). Só
# trocas SEGURAS: sinônimos comuns de legenda, saudações, emojis e pontuação.
# NÃO tocamos em #hashtags, @menções, links nem {spintax}.

# Sinônimos conservadores PT-BR (mantêm o sentido em legenda de rede social).
_SINONIMOS = {
    'linda': ['maravilhosa', 'perfeita', 'deslumbrante', 'um encanto'],
    'lindo': ['maravilhoso', 'perfeito', 'um encanto'],
    'lindas': ['maravilhosas', 'perfeitas', 'deslumbrantes'],
    'amei': ['adorei', 'amei demais', 'apaixonei'],
    'amo': ['adoro', 'amo demais'],
    'gente': ['pessoal', 'galera', 'gente'],
    'hoje': ['hoje', 'neste dia'],
    'muito': ['super', 'bastante', 'muito'],
    'top': ['incrível', 'sensacional', 'top demais'],
    'legal': ['bacana', 'massa', 'show'],
    'novo': ['novo', 'fresquinho'],
    'nova': ['nova', 'fresquinha'],
    'olha': ['olha', 'repara', 'vê só'],
    'vem': ['vem', 'chega'],
    'delícia': ['delícia', 'perfeição', 'tudo de bom'],
    'bom': ['bom', 'ótimo'],
    'boa': ['boa', 'ótima'],
    'feliz': ['feliz', 'radiante', 'contente'],
    'saudade': ['saudade', 'sdds'],
    'sonho': ['sonho', 'perfeição'],
    'incrível': ['incrível', 'sensacional', 'espetacular'],
    'perfeito': ['perfeito', 'impecável', 'certeiro'],
    'perfeita': ['perfeita', 'impecável'],
    'maravilhoso': ['maravilhoso', 'espetacular', 'sensacional'],
    'maravilhosa': ['maravilhosa', 'espetacular', 'sensacional'],
    'obrigado': ['obrigado', 'valeu', 'grato'],
    'obrigada': ['obrigada', 'valeu', 'grata'],
    'demais': ['demais', 'pra caramba', 'muito'],
}

# Saudações (frases de abertura) — só trocamos quando aparecem no COMEÇO.
_SAUDACOES = {
    'bom dia': ['bom diaa', 'dia lindo', 'bom dia pra você'],
    'boa tarde': ['boa tardee', 'tarde linda', 'boa tarde pra você'],
    'boa noite': ['boa noitee', 'noite linda', 'boa noite pra você'],
    'oi': ['olá', 'oiê', 'opa', 'e aí'],
    'olá': ['oi', 'oiê', 'opa'],
    'oie': ['oiê', 'oi', 'opa'],
}

_EMOJIS = ['✨', '💫', '😍', '🥰', '💕', '💖', '🔥', '😌', '🌸', '🦋',
           '💗', '🤍', '⭐', '🌟', '😊', '💛', '🌷', '💐', '👏', '🙌']

# Token "intocável": #hashtag, @menção ou link — nunca variamos.
_INTOCAVEL = re.compile(r'^(?:[#@]\w|https?://|www\.)')
_EMOJI_RANGE = re.compile(
    '[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿⬀-⯿️]'
)


def _troca_palavra(pal, rng):
    """Troca uma palavra por um sinônimo (mantendo maiúscula/pontuação colada)."""
    m = re.match(r'^(\W*)(\w[\w\-]*)(\W*)$', pal, re.UNICODE)
    if not m:
        return pal
    pre, nucleo, pos = m.groups()
    opcoes = _SINONIMOS.get(nucleo.lower())
    if not opcoes:
        return pal
    novo = rng.choice(opcoes)
    if nucleo[:1].isupper():
        novo = novo[:1].upper() + novo[1:]
    return f"{pre}{novo}{pos}"


def _varia_emojis(texto, rng):
    """Troca alguns emojis existentes por outros do pool e, às vezes, acrescenta
    1 emoji no fim (nunca mexe em texto/hashtag)."""
    def _sub(m):
        # 60% das vezes troca por outro emoji do pool.
        return rng.choice(_EMOJIS) if rng.random() < 0.6 else m.group(0)
    texto = _EMOJI_RANGE.sub(_sub, texto)
    # 55% das vezes acrescenta um emoji no fim (se não terminar já com um).
    if rng.random() < 0.55 and not _EMOJI_RANGE.search(texto[-2:]):
        texto = texto.rstrip() + ' ' + rng.choice(_EMOJIS)
    return texto


def _troca_saudacao(texto, rng):
    """Se a legenda começa com uma saudação conhecida, troca por variante."""
    baixo = texto.lstrip()
    desloc = len(texto) - len(baixo)
    for saud, ops in sorted(_SAUDACOES.items(), key=lambda kv: -len(kv[0])):
        if baixo.lower().startswith(saud):
            resto = baixo[len(saud):]
            nova = rng.choice(ops)
            if baixo[:1].isupper():
                nova = nova[:1].upper() + nova[1:]
            return texto[:desloc] + nova + resto
    return texto


def variar_semantica(texto, rng):
    """Reescreve mantendo o SENTIDO: sinônimos + saudação + emojis + pontuação.
    Determinístico pelo rng. Protege #hashtags, @menções e links."""
    if not texto or not texto.strip():
        return texto

    # Separa o bloco de hashtags do fim (se houver) para não misturar.
    linhas = texto.split('\n')
    corpo, cauda = texto, ''
    if len(linhas) >= 2 and linhas[-1].strip().startswith('#'):
        corpo = '\n'.join(linhas[:-1])
        cauda = '\n' + linhas[-1]

    corpo = _troca_saudacao(corpo, rng)

    # Troca sinônimos em ~50% das palavras elegíveis (conservador).
    saida = []
    for tok in re.split(r'(\s+)', corpo):
        if not tok.strip() or _INTOCAVEL.match(tok):
            saida.append(tok)
            continue
        if rng.random() < 0.5:
            saida.append(_troca_palavra(tok, rng))
        else:
            saida.append(tok)
    corpo = ''.join(saida)

    corpo = _varia_emojis(corpo, rng)

    # Pontuação final: alterna entre "!", "!!", "…", "." de forma leve.
    corpo = re.sub(r'([!.]){1,3}(\s*)$',
                   lambda m: rng.choice(['!', '!!', '…', '.', ' ✨']) + m.group(2),
                   corpo)

    return corpo + cauda


def _so_invisivel(texto):
    """O texto sobrou só com espaço/quebra/caracteres invisíveis?

    Não basta `.strip()`: a variação pode deixar zero-width space (U+200B),
    word joiner (U+2060) e afins, que o Instagram trata como legenda vazia.
    """
    if not texto:
        return True
    return not texto.strip('​‌‍⁠﻿ \t\r\n')


def variar_legenda(caption, seed, semantica=None):
    """Devolve a legenda variada e determinística por conta+post.

    Camadas:
      1. SPINTAX `{a|b|c}` — variação REAL quando o usuário fornece opções.
      2. SEMÂNTICA (algoritmo local, sem IA) — sinônimos/saudação/emoji/pontuação
         mantendo o sentido, para o texto ficar único por conta mesmo sem spintax.

    O IG remove texto por CONFIANÇA da conta, mas legenda IDÊNTICA em massa é um
    gatilho a mais — variar reduz esse sinal (não cura conta já flagrada).
    `semantica`: liga/desliga a camada 2 (default = settings.VARIAR_LEGENDA_SEMANTICA).
    """
    if not caption or not caption.strip():
        return caption
    rng = _rng(seed)
    out = expandir_spintax(caption, rng)
    if semantica is None:
        from django.conf import settings
        semantica = getattr(settings, 'VARIAR_LEGENDA_SEMANTICA', True)
    if semantica:
        out = variar_semantica(out, rng)

    # REDE DE SEGURANÇA: a variação NUNCA pode zerar uma legenda que o usuário
    # escreveu. Spintax com opção vazia — `{oi|}`, `{a|}`, `{ | }` — é legítimo
    # no meio do texto ("oi {amigo|}" vira "oi" ou "oi amigo"), mas quando o
    # spintax é a legenda INTEIRA o ramo vazio apagava tudo. Como o sorteio é
    # por conta (seed = conta+post), uma parte das contas publicava com texto e
    # a outra sem — foi o relato de "algumas postagens estão indo sem legenda".
    # Aqui, se sobrou só espaço/invisível, devolvemos o texto original.
    if _so_invisivel(out):
        return caption
    return out
