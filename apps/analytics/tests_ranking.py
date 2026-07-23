"""Ranking do dia: números do DIA e sem inflar por causa de join.

O bug que estes testes travam: anotar posts (scheduledpost) e métricas de
conta (instagramaccount) no mesmo queryset faz o Django cruzar as tabelas e
multiplicar tudo pelo número de contas. Medido em produção: 2.532 posts no
lugar de 211 e 1,4 bilhão de views no lugar de 807 mil.

    python manage.py test apps.analytics.tests_ranking
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.instagram.models import InstagramAccount
from apps.publisher.models import ScheduledPost


class RankingDoDiaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dono', password='x', is_active=True)
        self.client.force_login(self.user)
        agora = timezone.now()
        ontem = agora - timezone.timedelta(days=1)

        # 3 contas, 40 views hoje cada = 120 no total.
        self.contas = [
            InstagramAccount.objects.create(owner=self.user, ig_username=f'c{i}',
                                            views_today=40, views_total=1000,
                                            followers_count=10)
            for i in range(3)
        ]
        # 2 posts hoje em CADA uma das 2 primeiras contas = 4 posts, 2 contas ativas.
        for conta in self.contas[:2]:
            for _ in range(2):
                ScheduledPost.objects.create(
                    owner=self.user, account=conta, post_type='REELS',
                    status='published', scheduled_for=agora, published_at=agora)
        # 5 posts de ONTEM não podem entrar na conta do dia.
        for _ in range(5):
            ScheduledPost.objects.create(
                owner=self.user, account=self.contas[2], post_type='REELS',
                status='published', scheduled_for=ontem, published_at=ontem)

    def _item(self):
        resp = self.client.get(reverse('analytics:dashboard'))
        self.assertEqual(resp.status_code, 200)
        ranking = resp.context['ranking_list']
        self.assertEqual(len(ranking), 1)
        return ranking[0]

    def test_posts_nao_sao_multiplicados_pelo_numero_de_contas(self):
        """4 posts reais — não 4x3=12."""
        self.assertEqual(self._item()['posts'], 4)

    def test_conta_apenas_os_posts_de_hoje(self):
        """Os 5 de ontem ficam de fora."""
        self.assertEqual(self._item()['posts'], 4)

    def test_contas_do_dia_sao_as_que_postaram_hoje(self):
        """3 contas cadastradas, mas só 2 postaram hoje."""
        self.assertEqual(self._item()['contas'], 2)

    def test_views_do_dia_nao_sao_multiplicadas_pelos_posts(self):
        """3 contas x 40 = 120 — não 120 x 4 posts."""
        self.assertEqual(self._item()['views_hoje'], 120)

    def test_card_de_contas_mostra_as_ativas_do_dia(self):
        resp = self.client.get(reverse('analytics:dashboard'))
        self.assertEqual(resp.context['contas_ativas_hoje'], 2)
        self.assertEqual(resp.context['accounts_count'], 3)

    def test_quem_nao_publicou_hoje_fica_fora_do_ranking(self):
        outro = User.objects.create_user(username='parado', password='x', is_active=True)
        InstagramAccount.objects.create(owner=outro, ig_username='zzz', views_today=999)
        nomes = [i['name'] for i in self._item() and
                 self.client.get(reverse('analytics:dashboard')).context['ranking_list']]
        self.assertNotIn(outro.display_name, nomes)
