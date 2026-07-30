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


def variar_legenda(caption, seed):
    """Devolve a legenda com o spintax `{a|b|c}` resolvido (variação REAL de
    texto, determinística por conta+post).

    NÃO usamos mais truque de caractere invisível: testes mostraram que o
    Instagram remove o texto por CONFIANÇA da conta, não pelo texto ser único
    (uma legenda única também some numa conta flagrada; uma conta confiável
    mantém legenda repetida). Invisível não descflagra e pode ser sinal ruim.
    A variação que ajuda de verdade é usar textos DIFERENTES por conta (spintax).
    """
    if not caption or not caption.strip():
        return caption
    return expandir_spintax(caption, _rng(seed))
