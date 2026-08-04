"""Filtros usados só na renderização da pressel."""
from django import template

register = template.Library()


@register.filter
def virgula_ponto(valor):
    """Float com PONTO decimal, sempre.

    O projeto roda em pt-BR com L10N ligado, então `0.45` sairia como `0,45` no
    template — e `brightness(0,45)` é CSS inválido (a página fica sem o
    escurecimento do fundo). Este filtro força o ponto.
    """
    try:
        return ('%f' % float(valor)).rstrip('0').rstrip('.') or '0'
    except (TypeError, ValueError):
        return '0'


@register.filter
def hex_rgba(cor_hex, alpha='1'):
    """#0088cc + '0.4'  ->  rgba(0, 136, 204, 0.4)

    O modelo usa o brilho do botão numa cor rgba derivada da cor sólida. Como a
    cor agora é escolhida pelo usuário, o brilho tem que acompanhar.
    """
    c = (cor_hex or '').strip().lstrip('#')
    if len(c) == 3:
        c = ''.join(ch * 2 for ch in c)
    if len(c) != 6:
        return 'rgba(0,0,0,0)'
    try:
        r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return 'rgba(0,0,0,0)'
    try:
        a = float(alpha)
    except (TypeError, ValueError):
        a = 1.0
    return f'rgba({r}, {g}, {b}, {a})'
