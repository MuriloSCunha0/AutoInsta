"""Fila x Histórico: publicado sai da fila e não pode ser atingido por engano.

O risco que estes testes travam: "selecionar todas" na fila mandava uma flag
e o servidor resolvia por query. Se essa query não respeitar o escopo da tela,
um "excluir todas" na fila apagaria TAMBÉM todo o histórico de publicados —
que a tela nem estava mostrando.

    python manage.py test apps.publisher.tests_fila_historico
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.instagram.models import InstagramAccount
from apps.publisher.models import ScheduledPost


class FilaHistoricoTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dono', password='x', is_active=True)
        self.client.force_login(self.user)
        self.conta = InstagramAccount.objects.create(owner=self.user, ig_username='conta_teste')
        agora = timezone.now()
        self.pendentes = [
            ScheduledPost.objects.create(owner=self.user, account=self.conta, post_type='REELS',
                                         status='queued', scheduled_for=agora, caption=f'pend{i}')
            for i in range(3)
        ]
        self.publicados = [
            ScheduledPost.objects.create(owner=self.user, account=self.conta, post_type='REELS',
                                         status='published', scheduled_for=agora,
                                         published_at=agora, caption=f'pub{i}')
            for i in range(4)
        ]

    def test_fila_nao_mostra_publicados(self):
        resp = self.client.get(reverse('publisher:queue'))
        self.assertEqual(resp.status_code, 200)
        ids = {p.id for p in resp.context['posts']}
        self.assertEqual(ids, {p.id for p in self.pendentes})
        self.assertEqual(resp.context['total_publicados'], 4)

    def test_historico_mostra_so_publicados(self):
        resp = self.client.get(reverse('publisher:historico'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual({p.id for p in resp.context['posts']},
                         {p.id for p in self.publicados})

    def test_stories_nao_mostra_publicados(self):
        agora = timezone.now()
        vivo = ScheduledPost.objects.create(owner=self.user, account=self.conta, post_type='STORY',
                                            status='queued', scheduled_for=agora)
        ScheduledPost.objects.create(owner=self.user, account=self.conta, post_type='STORY',
                                     status='published', scheduled_for=agora, published_at=agora)
        resp = self.client.get(reverse('publisher:stories'))
        self.assertEqual({p.id for p in resp.context['posts']}, {vivo.id})

    def test_excluir_todas_da_fila_preserva_o_historico(self):
        """O caso que dói: não pode levar o histórico junto."""
        self.client.post(reverse('publisher:bulk_posts'),
                         {'acao': 'excluir', 'todos': '1', 'escopo': 'fila'})
        self.assertEqual(ScheduledPost.objects.filter(status='queued').count(), 0)
        self.assertEqual(ScheduledPost.objects.filter(status='published').count(), 4)

    def test_excluir_todas_do_historico_preserva_a_fila(self):
        self.client.post(reverse('publisher:bulk_posts'),
                         {'acao': 'excluir', 'todos': '1', 'escopo': 'historico'})
        self.assertEqual(ScheduledPost.objects.filter(status='published').count(), 0)
        self.assertEqual(ScheduledPost.objects.filter(status='queued').count(), 3)

    def test_escopo_ausente_nao_alcanca_publicados(self):
        """Sem escopo declarado, o padrão seguro é a fila."""
        self.client.post(reverse('publisher:bulk_posts'), {'acao': 'excluir', 'todos': '1'})
        self.assertEqual(ScheduledPost.objects.filter(status='published').count(), 4)

    def test_dashboard_lista_pendentes_e_nao_publicados(self):
        resp = self.client.get(reverse('analytics:dashboard'))
        self.assertEqual(resp.status_code, 200)
        for post in resp.context['recent_posts']:
            self.assertIn(post.status, ScheduledPost.STATUS_ATIVOS)


class FiltroPorContaTest(TestCase):
    """Filtro por conta na fila: ver e operar só a conta escolhida."""

    def setUp(self):
        self.user = User.objects.create_user(username='dono', password='x', is_active=True)
        self.client.force_login(self.user)
        self.a = InstagramAccount.objects.create(owner=self.user, ig_username='conta_a')
        self.b = InstagramAccount.objects.create(owner=self.user, ig_username='conta_b')
        agora = timezone.now()
        for _ in range(3):
            ScheduledPost.objects.create(owner=self.user, account=self.a, post_type='REELS',
                                         status='queued', scheduled_for=agora)
        for _ in range(2):
            ScheduledPost.objects.create(owner=self.user, account=self.b, post_type='REELS',
                                         status='queued', scheduled_for=agora)

    def test_filtra_a_fila_pela_conta(self):
        resp = self.client.get(reverse('publisher:queue'), {'account': self.a.id})
        self.assertEqual(resp.status_code, 200)
        contas = {p.account_id for p in resp.context['posts']}
        self.assertEqual(contas, {self.a.id})
        self.assertEqual(resp.context['total_filtrado'], 3)

    def test_excluir_todas_da_conta_nao_toca_nas_outras(self):
        """O ponto crítico: 'selecionar todas' com conta filtrada não pode
        apagar posts de outra conta."""
        self.client.post(reverse('publisher:bulk_posts'),
                         {'acao': 'excluir', 'todos': '1', 'escopo': 'fila',
                          'account': str(self.a.id)})
        self.assertEqual(ScheduledPost.objects.filter(account=self.a).count(), 0)
        self.assertEqual(ScheduledPost.objects.filter(account=self.b).count(), 2)

    def test_sem_filtro_de_conta_opera_todas(self):
        self.client.post(reverse('publisher:bulk_posts'),
                         {'acao': 'excluir', 'todos': '1', 'escopo': 'fila'})
        self.assertEqual(ScheduledPost.objects.filter(status='queued').count(), 0)


class FilaPorDiaEPastaTest(TestCase):
    """Fila agrupada por dia e filtro por pasta."""

    def setUp(self):
        from apps.instagram.models import Pasta
        self.user = User.objects.create_user(username='dono', password='x', is_active=True)
        self.client.force_login(self.user)
        self.pasta = Pasta.objects.create(owner=self.user, name='P1')
        self.a = InstagramAccount.objects.create(owner=self.user, ig_username='a', pasta=self.pasta)
        self.b = InstagramAccount.objects.create(owner=self.user, ig_username='b')  # sem pasta
        agora = timezone.now()
        # a: hoje e amanhã; b: hoje
        ScheduledPost.objects.create(owner=self.user, account=self.a, post_type='REELS',
                                     status='queued', scheduled_for=agora + timezone.timedelta(minutes=10))
        ScheduledPost.objects.create(owner=self.user, account=self.a, post_type='REELS',
                                     status='queued', scheduled_for=agora + timezone.timedelta(days=1))
        ScheduledPost.objects.create(owner=self.user, account=self.b, post_type='REELS',
                                     status='queued', scheduled_for=agora + timezone.timedelta(minutes=20))

    def test_agrupa_por_dia_hoje_amanha(self):
        resp = self.client.get(reverse('publisher:queue'))
        rotulos = [g['rotulo'] for g in resp.context['grupos_dia']]
        self.assertIn('Hoje', rotulos)
        self.assertIn('Amanhã', rotulos)
        self.assertLess(rotulos.index('Hoje'), rotulos.index('Amanhã'))  # hoje antes

    def test_filtra_fila_por_pasta(self):
        resp = self.client.get(reverse('publisher:queue'), {'pasta': self.pasta.id})
        contas = {p.account_id for p in resp.context['posts']}
        self.assertEqual(contas, {self.a.id})  # só a conta da pasta

    def test_excluir_todas_com_pasta_nao_toca_fora(self):
        self.client.post(reverse('publisher:bulk_posts'),
                         {'acao': 'excluir', 'todos': '1', 'escopo': 'fila',
                          'pasta': str(self.pasta.id)})
        self.assertEqual(ScheduledPost.objects.filter(account=self.a).count(), 0)
        self.assertEqual(ScheduledPost.objects.filter(account=self.b).count(), 1)


class ProximasHTMXTest(TestCase):
    def test_endpoint_proximas_retorna_pendentes(self):
        u = User.objects.create_user(username='dono', password='x', is_active=True)
        self.client.force_login(u)
        acc = InstagramAccount.objects.create(owner=u, ig_username='a')
        agora = timezone.now()
        ScheduledPost.objects.create(owner=u, account=acc, post_type='REELS',
                                     status='queued', scheduled_for=agora)
        ScheduledPost.objects.create(owner=u, account=acc, post_type='REELS',
                                     status='published', scheduled_for=agora, published_at=agora)
        resp = self.client.get(reverse('analytics:proximas'))
        self.assertEqual(resp.status_code, 200)
        # só o queued aparece (o published saiu da fila)
        self.assertEqual(len(resp.context['recent_posts']), 1)
