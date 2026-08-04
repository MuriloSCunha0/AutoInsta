"""Aviso de madrugada: publicar de 00h às 05h é o padrão mais robótico que existe.

Medido em produção (04/08/2026): a curva de publicações por hora estava PLANA
nas 24h — 396 posts às 03h contra 370 ao meio-dia. Conta real não posta às 4h
todo dia. Não bloqueamos (a fila é do usuário), mas avisamos em dois momentos:
ao escolher o horário no composer e ao criar a fila.

    python manage.py test apps.publisher.tests_madrugada
"""
from datetime import datetime

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from apps.core_utils import aviso_madrugada, e_madrugada


def _local(hora, minuto=0):
    """Um datetime AWARE no fuso do projeto (America/Sao_Paulo)."""
    ingenuo = datetime(2026, 8, 4, hora, minuto)
    return timezone.make_aware(ingenuo, timezone.get_current_timezone())


class EMadrugadaTest(SimpleTestCase):
    def test_horas_da_madrugada(self):
        for h in (0, 1, 2, 3, 4):
            with self.subTest(hora=h):
                self.assertTrue(e_madrugada(_local(h)))

    def test_horas_normais(self):
        for h in (5, 8, 12, 18, 21, 23):
            with self.subTest(hora=h):
                self.assertFalse(e_madrugada(_local(h)))

    def test_none_nao_quebra(self):
        self.assertFalse(e_madrugada(None))

    @override_settings(MADRUGADA_INI=22, MADRUGADA_FIM=6)
    def test_faixa_que_cruza_a_meia_noite(self):
        self.assertTrue(e_madrugada(_local(23)))
        self.assertTrue(e_madrugada(_local(2)))
        self.assertFalse(e_madrugada(_local(12)))
        self.assertFalse(e_madrugada(_local(21)))


class AvisoMadrugadaTest(SimpleTestCase):
    def test_sem_horario_na_madrugada_nao_avisa(self):
        self.assertIsNone(aviso_madrugada([_local(9), _local(15), _local(20)]))

    def test_lista_vazia_nao_avisa(self):
        self.assertIsNone(aviso_madrugada([]))

    def test_avisa_e_conta_quantos(self):
        aviso = aviso_madrugada([_local(9), _local(2), _local(3), _local(14)])
        self.assertIsNotNone(aviso)
        self.assertIn('2 publicação', aviso)

    def test_texto_explica_o_motivo(self):
        aviso = aviso_madrugada([_local(3)]).lower()
        self.assertIn('madrugada', aviso)
        self.assertIn('automação', aviso)
