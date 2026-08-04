# -*- coding: utf-8 -*-
"""Bloqueio das abas escondidas.

Sumir com o link do menu não basta: quem digitar /library/downloader/ entra
assim mesmo. Este middleware fecha a porta usando o MESMO registro de abas que
o menu — se um dia alguém acrescentar uma rota nova ao registro, os dois lados
passam a conhecê-la junto.
"""
from django.contrib import messages
from django.shortcuts import redirect


class AbasOcultasMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        # `process_view` roda DEPOIS da resolução da URL, então já temos o
        # namespace/url_name — que é o que o registro de abas usa.
        from apps.accounts.abas import aba_da_rota

        usuario = getattr(request, 'user', None)
        if not usuario or not usuario.is_authenticated:
            return None

        ocultas = usuario.abas_ocultas_set
        if not ocultas:
            return None

        match = request.resolver_match
        if not match:
            return None

        aba = aba_da_rota(match.namespace, match.url_name)
        if aba and aba in ocultas:
            messages.warning(
                request,
                'Essa área não está liberada no seu acesso. '
                'Fale com o suporte se precisar dela.')
            return redirect('root')
        return None
