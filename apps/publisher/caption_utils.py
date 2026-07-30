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

# Caracteres invisíveis (largura zero) usados para tornar a legenda única.
_ZW = ['​', '‌', '⁠']


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


def _neutra(palavra):
    """Palavra 'neutra' onde é seguro colar um caractere invisível no fim
    (não é hashtag, menção nem link)."""
    return bool(palavra) and not palavra.startswith(('#', '@', 'http'))


def variar_legenda(caption, seed):
    """Devolve uma versão ÚNICA por conta/post da legenda.

    - Expande spintax `{a|b|c}`.
    - Insere 1-3 caracteres invisíveis em palavras neutras.
    - Varia as quebras de linha no final (0-2).
    Se a legenda for vazia, devolve como está.
    """
    if not caption or not caption.strip():
        return caption

    rng = _rng(seed)
    txt = expandir_spintax(caption, rng)

    palavras = txt.split(' ')
    candidatos = [i for i, w in enumerate(palavras) if _neutra(w) and len(w) > 1]
    if candidatos:
        qtd = min(len(candidatos), rng.randint(1, 3))
        for i in rng.sample(candidatos, qtd):
            palavras[i] = palavras[i] + rng.choice(_ZW)
    txt = ' '.join(palavras)

    # Variação sutil no final (algumas contas com 0, outras com 1-2 quebras).
    txt = txt.rstrip() + ('\n' * rng.randint(0, 2))
    return txt
