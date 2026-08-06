"""Faxina do processed/: a task apaga cópias transitórias ANTIGAS e preserva as
recentes (em voo). Sem ela o painel enchia ~30GB/dia até estourar o disco.

    python manage.py test apps.publisher.tests_faxina
"""
import os
import time
import tempfile

from django.test import TestCase, override_settings

from apps.publisher.tasks import limpar_midia_processada


class FaxinaProcessedTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.proc = os.path.join(self.tmp, 'processed')
        os.makedirs(self.proc)

    def _cria(self, nome, horas_atras):
        cam = os.path.join(self.proc, nome)
        with open(cam, 'wb') as fh:
            fh.write(b'x' * 1024)
        t = time.time() - horas_atras * 3600
        os.utime(cam, (t, t))
        return cam

    def test_apaga_antigo_preserva_recente(self):
        antigo = self._cria('velho.mp4', 10)   # 10h atrás
        recente = self._cria('novo.mp4', 1)     # 1h atrás
        with override_settings(MEDIA_ROOT=self.tmp, PROCESSED_TTL_HORAS=6):
            r = limpar_midia_processada()
        self.assertFalse(os.path.exists(antigo))   # >6h: apagado
        self.assertTrue(os.path.exists(recente))   # <6h: fica (pode estar em voo)
        self.assertEqual(r['apagados'], 1)

    def test_sem_pasta_nao_quebra(self):
        with override_settings(MEDIA_ROOT='/caminho/que/nao/existe',
                               PROCESSED_TTL_HORAS=6):
            r = limpar_midia_processada()
        self.assertEqual(r['apagados'], 0)

    def test_nada_antigo_nao_apaga(self):
        self._cria('a.mp4', 1)
        self._cria('b.mp4', 2)
        with override_settings(MEDIA_ROOT=self.tmp, PROCESSED_TTL_HORAS=6):
            r = limpar_midia_processada()
        self.assertEqual(r['apagados'], 0)
        self.assertEqual(len(os.listdir(self.proc)), 2)
