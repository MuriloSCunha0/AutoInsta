"""Métricas têm de vir de dado real — nada de número inventado.

Duas fabricações existiam aqui e estes testes impedem que voltem:
  - Top Posts: `views = like_count * 8` (multiplicador inventado). Com 0 like
    mostrava 0 views quando o real, medido na Meta, eram 87.
  - Performance: `engagement_rate = posts_total * 0.1` ("dummy formula").

    python manage.py test apps.analytics.tests_metricas
"""
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.db.models import Sum

from apps.accounts.models import User
from apps.analytics.views import _views_da_midia
from apps.instagram.models import InstagramAccount


class ViewsDaMidiaTest(SimpleTestCase):
    """Lê o formato real de `insights.metric(views)` devolvido pela Meta."""

    def test_le_o_valor_real_da_meta(self):
        media = {'like_count': 0, 'insights': {'data': [
            {'name': 'views', 'values': [{'value': 87}]}]}}
        self.assertEqual(_views_da_midia(media), 87)

    def test_nao_deriva_views_de_likes(self):
        """O caso que expôs a fabricação: 0 like, 87 views reais."""
        media = {'like_count': 0, 'insights': {'data': [
            {'name': 'views', 'values': [{'value': 87}]}]}}
        self.assertNotEqual(_views_da_midia(media), media['like_count'] * 8)

    def test_sem_insights_devolve_zero_em_vez_de_estimar(self):
        self.assertEqual(_views_da_midia({'like_count': 50}), 0)

    def test_tolera_formatos_incompletos(self):
        for media in ({}, {'insights': {}}, {'insights': {'data': []}},
                      {'insights': {'data': [{'name': 'views', 'values': []}]}},
                      {'insights': {'data': [{'name': 'views', 'values': [{'value': None}]}]}}):
            self.assertEqual(_views_da_midia(media), 0)


class PerformanceMetricasTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dono', password='x', is_active=True)
        self.client.force_login(self.user)
        InstagramAccount.objects.create(owner=self.user, ig_username='a',
                                        followers_count=100, posts_count=10,
                                        views_total=5000, views_today=300)
        InstagramAccount.objects.create(owner=self.user, ig_username='b',
                                        followers_count=50, posts_count=10,
                                        views_total=3000, views_today=200)

    def test_mostra_views_reais_e_nao_taxa_inventada(self):
        resp = self.client.get(reverse('analytics:performance'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('engagement_rate', resp.context)
        self.assertEqual(resp.context['views_total'], 8000)
        self.assertEqual(resp.context['views_today'], 500)

    def test_media_de_views_por_post_vem_dos_dados(self):
        resp = self.client.get(reverse('analytics:performance'))
        # 8000 views / 20 posts = 400
        self.assertEqual(resp.context['views_por_post'], 400)

    def test_sem_post_nao_divide_por_zero(self):
        InstagramAccount.objects.filter(owner=self.user).update(posts_count=0)
        resp = self.client.get(reverse('analytics:performance'))
        self.assertEqual(resp.context['views_por_post'], 0)

    def test_somas_nao_sao_infladas_por_join(self):
        """Confere contra o valor cru do banco."""
        real = (InstagramAccount.objects.filter(owner=self.user)
                .aggregate(v=Sum('views_total'))['v'])
        resp = self.client.get(reverse('analytics:performance'))
        self.assertEqual(resp.context['views_total'], real)
