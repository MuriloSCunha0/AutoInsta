def abas(request):
    """Injeta as abas escondidas do usuário para o menu lateral filtrar.

    Vem como CONJUNTO para o template poder usar `{% if 'cta' not in abas_ocultas %}`
    — mais curto e mais legível que uma tag customizada em ~20 itens de menu.
    """
    usuario = getattr(request, 'user', None)
    if not usuario or not usuario.is_authenticated:
        return {'abas_ocultas': set()}
    return {'abas_ocultas': usuario.abas_ocultas_set}
