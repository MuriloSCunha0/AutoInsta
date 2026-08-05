"""Story é isento do limite; e o limite recomenda em vez de reagendar.

Reclamações reais (05/08/2026):
  1. "Quando a conta é limitada, o Instagram não limita story, mas o sistema
     está limitando." — o dispatcher barrava TODOS os tipos no teto/cooldown.
  2. "O bloqueio era para recomendar ao usuário, não reagendar os posts." — o
     dispatcher movia o scheduled_for (a fila 'pulava' de horário sozinha).

    python manage.py test apps.publisher.tests_story_limite
"""
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from datetime import timedelta

from apps.accounts.models import User
from apps.instagram.models import InstagramAccount
from apps.publisher.models import ScheduledPost
from apps.publisher.tasks import process_scheduled_posts


# O alerta usa cache (Redis em produção). No teste não há Redis, então trocamos
# por um cache em memória — senão o `alertar` estoura e a recomendação é
# engolida (é a causa dos erros de tests_alertas no ambiente de teste).
@override_settings(CACHES={'default': {
    'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class StoryIsentoTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='iorio', password='x')
        self.now = timezone.now()

    def _post(self, conta, tipo, quando=None):
        p = ScheduledPost.objects.create(
            owner=self.user, account=conta, post_type=tipo, status='queued',
            scheduled_for=quando or (self.now - timedelta(minutes=1)))
        p.video_file.name = 'reels/x.mp4'
        p.save()
        return p

    def _rodar(self):
        """Roda o dispatcher capturando quais posts foram despachados."""
        despachados = []
        with mock.patch('apps.publisher.tasks.publish_reel.delay',
                        side_effect=lambda pid: despachados.append(pid)):
            process_scheduled_posts()
        return despachados

    def test_story_sai_mesmo_com_a_conta_em_cooldown(self):
        conta = InstagramAccount.objects.create(
            owner=self.user, ig_username='limitada', status='active',
            meta_access_token='t', ig_user_id=1, daily_post_limit=0,
            rate_limited_until=self.now + timedelta(hours=2))   # em cooldown
        story = self._post(conta, 'STORY')
        despachados = self._rodar()
        self.assertIn(story.id, despachados)

    def test_reels_nao_sai_com_a_conta_em_cooldown(self):
        conta = InstagramAccount.objects.create(
            owner=self.user, ig_username='limitada2', status='active',
            meta_access_token='t', ig_user_id=1, daily_post_limit=0,
            rate_limited_until=self.now + timedelta(hours=2))
        reels = self._post(conta, 'REELS')
        despachados = self._rodar()
        self.assertNotIn(reels.id, despachados)

    def test_reels_bloqueado_NAO_e_reagendado(self):
        # O ponto do usuário: o horário do post não pode mudar sozinho.
        conta = InstagramAccount.objects.create(
            owner=self.user, ig_username='limitada3', status='active',
            meta_access_token='t', ig_user_id=1, daily_post_limit=0,
            rate_limited_until=self.now + timedelta(hours=2))
        quando = self.now - timedelta(minutes=1)
        reels = self._post(conta, 'REELS', quando=quando)
        self._rodar()
        reels.refresh_from_db()
        # scheduled_for igual ao original (tolerância de 1s para arredondamento)
        self.assertAlmostEqual(reels.scheduled_for, quando,
                               delta=timedelta(seconds=1))

    def test_story_nao_conta_para_o_teto_diario(self):
        # Conta com teto 2 de FEED, já com 2 reels publicados hoje: story ainda sai.
        conta = InstagramAccount.objects.create(
            owner=self.user, ig_username='teto', status='active',
            meta_access_token='t', ig_user_id=1, daily_post_limit=2)
        for _ in range(2):
            ScheduledPost.objects.create(
                owner=self.user, account=conta, post_type='REELS',
                status='published', published_at=self.now - timedelta(minutes=5),
                scheduled_for=self.now - timedelta(minutes=5))
        story = self._post(conta, 'STORY')
        reels = self._post(conta, 'REELS')
        despachados = self._rodar()
        self.assertIn(story.id, despachados)       # story passa
        self.assertNotIn(reels.id, despachados)    # reels barrado (teto batido)

    def test_recomendacao_ao_bloquear_por_limite(self):
        from apps.notifications.models import Notification
        conta = InstagramAccount.objects.create(
            owner=self.user, ig_username='avisa', status='active',
            meta_access_token='t', ig_user_id=1, daily_post_limit=0,
            rate_limited_until=self.now + timedelta(hours=2))
        self._post(conta, 'REELS')
        self._rodar()
        # Uma recomendação foi criada para o dono (pref limite_atingido é on por padrão).
        self.assertTrue(
            Notification.objects.filter(user=self.user,
                                        title__icontains='limite').exists())
