"""Comentário `{# #}` do Django é de UMA LINHA só — multi-linha vaza na tela.

Bug visto em produção (04/08/2026): na Fila de Publicação apareceu, no lugar do
status do post, o texto:

    "{# Conta parada (pausada / de molho / caída / limitada): mostra o MOTIVO
     em vez de "na fila". Antes o post ficava eternamente..."

Da doc do Django: "This syntax can only be used for single-line comments (no
newlines are permitted between the {# and #} delimiters)." Quando há quebra de
linha, o `{#` deixa de ser comentário e o texto é renderizado como conteúdo.

Este teste varre TODOS os templates do projeto. Para comentário de várias
linhas: use vários `{# ... #}` de uma linha cada, ou `{% comment %}`.

    python manage.py test apps.publisher.tests_templates_comentario
"""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


def _templates():
    raizes = [Path(d) for d in settings.TEMPLATES[0]['DIRS']]
    raizes.append(Path(settings.BASE_DIR) / 'apps')
    for raiz in raizes:
        if raiz.exists():
            yield from raiz.rglob('*.html')


def _comentarios_multilinha(texto):
    """Linhas em que um `{#` abre e não fecha na mesma linha."""
    ruins = []
    for n, linha in enumerate(texto.splitlines(), 1):
        if '{#' not in linha:
            continue
        depois = linha.split('{#', 1)[1]
        if '#}' not in depois:
            ruins.append((n, linha.strip()[:80]))
    return ruins


class ComentarioDeTemplateTest(SimpleTestCase):
    def test_nenhum_comentario_multilinha(self):
        problemas = []
        for caminho in _templates():
            try:
                texto = caminho.read_text(encoding='utf-8')
            except (UnicodeDecodeError, OSError):
                continue
            for n, linha in _comentarios_multilinha(texto):
                problemas.append(f'{caminho}:{n}: {linha}')

        self.assertEqual(
            problemas, [],
            'Comentário {# #} multi-linha VAZA como texto na tela. '
            'Use vários {# #} de uma linha, ou {% comment %}:\n  '
            + '\n  '.join(problemas))

    def test_o_detector_pega_o_caso_real(self):
        # A regressão exata que apareceu na Fila de Publicação.
        ruim = '{# Conta parada (pausada / de molho):\n   mostra o MOTIVO. #}'
        self.assertTrue(_comentarios_multilinha(ruim))

    def test_o_detector_aceita_comentario_valido(self):
        bom = '{# uma linha só #}\n{# outra linha só #}\n<div>oi</div>'
        self.assertEqual(_comentarios_multilinha(bom), [])
