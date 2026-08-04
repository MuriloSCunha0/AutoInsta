"""Regressão: o modo de limpeza 'light' NUNCA pode voltar a processar mídia.

Contexto (investigação de 04/08/2026, produção): o 'light' rodava
`ffmpeg -c copy -metadata comment=<32 hex>` e o arquivo resultante saía com

    encoder=Lavf61.7.103          <- assinatura do ffmpeg
    comment=8f3a91b0c7d24e5f...   <- marca aleatória nossa

em 100% dos uploads (confirmado por ffprobe). Como era `-c copy`, o bitstream
ficava byte a byte igual ao original — não enganava fingerprint nenhum, só
trocava o MD5. Ou seja: carimbava todo upload com uma assinatura de ferramenta
de evasão e não protegia de nada.

Efeito medido, com dono/app/mídia/volume controlados (usuário KRN):
    1-5 posts em light  -> 19% das contas derrubadas (média 120 posts totais)
    21+ posts em light  -> 44% das contas derrubadas (média 102 posts totais)
Grupos puros: light 22/30 (73%) x none 3/22 (14%).

Estes testes travam as três defesas:
  1. 'light' não é mais uma opção válida do model;
  2. `limpar_video` devolve o ORIGINAL quando pedem 'light';
  3. o formulário normaliza qualquer 'light' recebido para 'none'.

    python manage.py test apps.publisher.tests_clean_mode_light
"""
import os
import tempfile

from django.test import SimpleTestCase

from apps.publisher.models import ScheduledPost
from apps.publisher.views import limpar_modo
from engine import media_cleaner


class CleanChoicesTest(SimpleTestCase):
    def test_light_nao_e_mais_uma_opcao(self):
        valores = [v for v, _ in ScheduledPost.CLEAN_CHOICES]
        self.assertNotIn('light', valores)
        self.assertEqual(valores, ['none', 'ultra'])

    def test_padrao_do_campo_e_none(self):
        campo = ScheduledPost._meta.get_field('clean_mode')
        self.assertEqual(campo.default, 'none')


class LimparVideoTest(SimpleTestCase):
    """`limpar_video` só processa em 'ultra'. Qualquer outro modo devolve o
    caminho original SEM tocar no arquivo — inclusive 'light', que pode vir de
    linhas antigas do banco."""

    def setUp(self):
        fd, self.arquivo = tempfile.mkstemp(suffix='.mp4')
        os.write(fd, b'bytes-do-video-original')
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.arquivo) and os.remove(self.arquivo))

    def test_light_devolve_o_arquivo_original_intacto(self):
        saida = media_cleaner.limpar_video(self.arquivo, mode='light', seed='conta-1')
        self.assertEqual(saida, self.arquivo)
        with open(self.arquivo, 'rb') as fh:
            self.assertEqual(fh.read(), b'bytes-do-video-original')

    def test_none_devolve_o_arquivo_original(self):
        self.assertEqual(
            media_cleaner.limpar_video(self.arquivo, mode='none', seed='conta-1'),
            self.arquivo,
        )

    def test_modo_desconhecido_devolve_o_original(self):
        self.assertEqual(
            media_cleaner.limpar_video(self.arquivo, mode='qualquer', seed='x'),
            self.arquivo,
        )

    def test_o_gerador_do_arquivo_carimbado_nao_existe_mais(self):
        # Era `_cmd_light`, que injetava `-metadata comment=<hex>`. Se alguém
        # recriar a função, este teste falha e força a revisão.
        self.assertFalse(hasattr(media_cleaner, '_cmd_light'))

    def test_padrao_da_funcao_e_none(self):
        # Antes o default era 'light': quem chamasse sem `mode` carimbava o
        # arquivo sem querer.
        self.assertEqual(
            media_cleaner.limpar_video.__defaults__[0], 'none'
        )


class LimparModoTest(SimpleTestCase):
    """O formulário nunca deve gravar 'light' de novo — nem se o navegador
    mandar (aba velha em cache, POST manual, plano antigo)."""

    def test_light_vira_none(self):
        self.assertEqual(limpar_modo('light'), 'none')

    def test_ultra_e_preservado(self):
        self.assertEqual(limpar_modo('ultra'), 'ultra')

    def test_none_continua_none(self):
        self.assertEqual(limpar_modo('none'), 'none')

    def test_vazio_ou_ausente_cai_no_padrao_seguro(self):
        for valor in ('', None, '   ', 'inventado'):
            self.assertEqual(limpar_modo(valor), 'none')
