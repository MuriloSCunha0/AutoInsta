"""Limitação de contas: pausar uma conta não pode travar a fila das outras,
e o teto efetivo respeita a cota real da Meta.

    python manage.py test apps.publisher.tests_limitacao
"""
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from apps.accounts.models import User
from apps.instagram.models import InstagramAccount
from apps.publisher.models import ScheduledPost


class ContaPausadaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dono', password='x', is_active=True)
        self.a = InstagramAccount.objects.create(owner=self.user, ig_username='a', status='active')
        self.b = InstagramAccount.objects.create(owner=self.user, ig_username='b', status='active')
        agora = timezone.now()
        self.pa = ScheduledPost.objects.create(owner=self.user, account=self.a, post_type='REELS',
                                               status='queued', scheduled_for=agora)
        self.pb = ScheduledPost.objects.create(owner=self.user, account=self.b, post_type='REELS',
                                               status='queued', scheduled_for=agora)

    def _rodar(self):
        with mock.patch('apps.publisher.tasks.publish_reel.delay') as d:
            from apps.publisher.tasks import process_scheduled_posts
            process_scheduled_posts()
            return {c.args[0] for c in d.call_args_list}

    def test_conta_pausada_nao_publica_mas_a_outra_sim(self):
        """O pedido do cliente: pausar a conta e a fila das OUTRAS continua."""
        self.a.pausada = True
        self.a.save()
        despachados = self._rodar()
        self.assertNotIn(self.pa.id, despachados)   # pausada não saiu
        self.assertIn(self.pb.id, despachados)      # a outra saiu normal

    def test_sem_pausa_ambas_publicam(self):
        despachados = self._rodar()
        self.assertEqual(despachados, {self.pa.id, self.pb.id})


class TetoEfetivoTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dono', password='x', is_active=True)

    def test_usa_a_cota_da_meta_quando_menor(self):
        """Usuário põe 500, mas a Meta só deixa 100 — o teto real é 100."""
        acc = InstagramAccount.objects.create(owner=self.user, ig_username='a',
                                              daily_post_limit=500, quota_total=100)
        self.assertEqual(acc.teto_efetivo, 100)

    def test_usa_o_limite_do_usuario_quando_menor(self):
        acc = InstagramAccount.objects.create(owner=self.user, ig_username='a',
                                              daily_post_limit=20, quota_total=100)
        self.assertEqual(acc.teto_efetivo, 20)

    def test_sem_limites_zero(self):
        acc = InstagramAccount.objects.create(owner=self.user, ig_username='a',
                                              daily_post_limit=0, quota_total=0)
        self.assertEqual(acc.teto_efetivo, 0)

    def test_no_teto_reagenda_para_quando_libera(self):
        """Post de conta no teto vai para o futuro, não fica vencido em loop."""
        acc = InstagramAccount.objects.create(owner=self.user, ig_username='a',
                                              status='active', daily_post_limit=2, quota_total=0)
        agora = timezone.now()
        # 2 publicados nas ultimas 24h = teto batido
        for h in (10, 5):
            ScheduledPost.objects.create(owner=self.user, account=acc, post_type='REELS',
                                         status='published', scheduled_for=agora,
                                         published_at=agora - timedelta(hours=h))
        p = ScheduledPost.objects.create(owner=self.user, account=acc, post_type='REELS',
                                         status='queued', scheduled_for=agora)
        with mock.patch('apps.publisher.tasks.publish_reel.delay') as d:
            from apps.publisher.tasks import process_scheduled_posts
            process_scheduled_posts()
            self.assertFalse(d.called)  # não publicou (no teto)
        p.refresh_from_db()
        self.assertGreater(p.scheduled_for, agora)  # foi remarcado para o futuro
