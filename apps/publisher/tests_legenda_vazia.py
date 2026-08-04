"""A variação NUNCA pode publicar sem legenda uma campanha que TINHA legenda.

Relato: "algumas postagens estão indo sem legenda". O "algumas" era a pista —
a variação é determinística por conta (seed = conta+post), então um spintax com
opção VAZIA fazia parte das contas sortear o ramo vazio:

    variar_legenda('{oi|}',  seed='conta-1')  ->  'oi'
    variar_legenda('{oi|}',  seed='conta-7')  ->  ''     <- publicava mudo

Se o spintax é a legenda INTEIRA, o ramo vazio apaga tudo. `{a|}` no MEIO do
texto continua legítimo ("oi {amigo|}" = "oi" ou "oi amigo") e segue funcionando.

Confirmado também que o campo enviado à Meta é `caption` (não existe
`spintax_caption` — nem na Graph API, nem no nosso código).

    python manage.py test apps.publisher.tests_legenda_vazia
"""
from django.test import SimpleTestCase

from apps.publisher.caption_utils import _so_invisivel, variar_legenda


class NuncaZeraALegendaTest(SimpleTestCase):
    def test_spintax_totalmente_vazio_devolve_o_original(self):
        for entrada in ('{oi|}', '{a|}', '{|}', '{ | }', '{|b}'):
            for i in range(30):          # varre várias contas/seeds
                with self.subTest(entrada=entrada, seed=i):
                    out = variar_legenda(entrada, seed=f'conta-{i}', semantica=True)
                    self.assertFalse(
                        _so_invisivel(out),
                        f'{entrada!r} com seed {i} publicaria sem legenda')

    def test_spintax_no_meio_do_texto_continua_opcional(self):
        # Aqui o ramo vazio é legítimo: "oi" ou "oi amigo". O que não pode é
        # a legenda inteira sumir.
        vistos = set()
        for i in range(30):
            out = variar_legenda('oi {amigo|}', seed=f'conta-{i}', semantica=False)
            self.assertFalse(_so_invisivel(out))
            vistos.add(out.strip())
        self.assertGreater(len(vistos), 1, 'o spintax deveria variar')

    def test_legenda_normal_nunca_vira_vazia(self):
        texto = 'Fique de olho nos storyes👀'
        for i in range(50):
            out = variar_legenda(texto, seed=f'conta-{i}-post-{i}', semantica=True)
            self.assertFalse(_so_invisivel(out), f'seed {i} zerou a legenda')

    def test_entrada_vazia_continua_vazia(self):
        # Quem não escreveu legenda continua sem legenda — não inventamos texto.
        for entrada in ('', '   ', None):
            self.assertFalse((variar_legenda(entrada, seed='x') or '').strip())


class SoInvisivelTest(SimpleTestCase):
    def test_detecta_vazio_de_verdade(self):
        for t in ('', '   ', '\n\t', None):
            self.assertTrue(_so_invisivel(t))

    def test_detecta_caracteres_invisiveis(self):
        # A variação injeta zero-width; o Instagram lê isso como legenda vazia.
        for t in ('​', '​‌‍', '⁠', '﻿', ' ​ '):
            with self.subTest(repr(t)):
                self.assertTrue(_so_invisivel(t))

    def test_texto_de_verdade_passa(self):
        for t in ('oi', 'a', '👀', '​oi​'):
            with self.subTest(repr(t)):
                self.assertFalse(_so_invisivel(t))
