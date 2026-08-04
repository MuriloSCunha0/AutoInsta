"""Card da conta DE MOLHO: retomar a fila OU apagar a fila.

Quando a Meta limita 2x seguidas, o sistema pausa a conta e reagenda a fila para
amanhã (comportamento automático, mantido). O card só oferecia "Retomar fila" —
faltava a outra saída: jogar a fila fora e recomeçar do zero, sem arrastar o
backlog velho.

Também trava o automático de 1 limite só: cooldown + retomada sozinha, SEM
pausar a conta.

    python manage.py test apps.instagram.tests_card_de_molho
"""
from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.instagram.models import InstagramAccount
from apps.publisher.models import ScheduledPost


class CardDeMolhoTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dono', password='x')
        self.acc = InstagramAccount.objects.create(
            owner=self.user, ig_username='conta', status='active',
            meta_access_token='t', ig_user_id=9,
            pausada=True, meta_limit_count=2)      # de molho
        for _ in range(3):
            ScheduledPost.objects.create(
                owner=self.user, account=self.acc, post_type='REELS',
                status='queued', scheduled_for=timezone.now())

    def _html(self):
        return render_to_string('instagram/partials/account_card.html',
                                {'account': self.acc})

    def test_mostra_que_esta_de_molho(self):
        self.assertIn('Conta de molho', self._html())

    def test_tem_botao_de_retomar(self):
        self.assertIn('Retomar fila', self._html())

    def test_tem_botao_de_apagar_a_fila(self):
        html = self._html()
        self.assertIn('Apagar fila', html)
        self.assertIn(f'/instagram/{self.acc.id}/zerar-fila/', html)

    def test_mostra_quantos_posts_serao_apagados(self):
        # O usuário precisa saber o tamanho do estrago antes de confirmar.
        html = self._html()
        self.assertIn('Apagar fila (3)', html)
        self.assertIn('3 post(s) esperando', html.replace('<strong>', '').replace('</strong>', ''))

    def test_sem_fila_nao_oferece_apagar(self):
        ScheduledPost.objects.filter(account=self.acc).delete()
        html = self._html()
        self.assertNotIn('Apagar fila', html)
        self.assertIn('Retomar fila', html)   # retomar continua fazendo sentido


class LimiteSimplesContinuaAutomaticoTest(TestCase):
    """1 limite só: cooldown e volta sozinha — a conta NÃO é pausada."""

    def setUp(self):
        self.user = User.objects.create_user(username='d2', password='x')
        self.acc = InstagramAccount.objects.create(
            owner=self.user, ig_username='c2', status='active',
            meta_access_token='t', ig_user_id=8,
            meta_limit_count=1,
            rate_limited_until=timezone.now() + timezone.timedelta(hours=3))

    def test_nao_fica_pausada_no_primeiro_limite(self):
        self.assertFalse(self.acc.pausada)
        self.assertFalse(self.acc.de_molho)

    def test_card_diz_que_volta_sozinha(self):
        html = render_to_string('instagram/partials/account_card.html',
                                {'account': self.acc})
        self.assertIn('Limitada pela Meta', html)

    def test_motivo_parada_explica_que_volta_sozinha(self):
        rotulo, explicacao = self.acc.motivo_parada
        self.assertEqual(rotulo, 'limitada')
        self.assertIn('sozinha', explicacao)
