"""O total de PUBLICADOS no dashboard não pode cair quando o usuário apaga
registros do histórico. Queixa do usuário: ao limpar os publicados, o número
diminuía. Solução: contador persistente por conta (publicados_total) que só
incrementa ao publicar — desacoplado do COUNT vivo de linhas.

    python manage.py test apps.publisher.tests_dashboard_publicados
"""
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.instagram.models import InstagramAccount
from apps.publisher.models import ScheduledPost
from apps.publisher.tasks import publish_reel


class DashboardPublicadosTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dono', password='x', is_active=True)
        self.client.force_login(self.user)
        self.acc = InstagramAccount.objects.create(
            owner=self.user, ig_username='c', status='active',
            meta_access_token='t', ig_user_id=1, publicados_total=5)

    def test_apagar_publicado_nao_derruba_o_total(self):
        # Existem 2 registros publicados; o contador persistente já vale 5.
        for _ in range(2):
            ScheduledPost.objects.create(
                owner=self.user, account=self.acc, post_type='REELS',
                status='published', scheduled_for=timezone.now(),
                published_at=timezone.now())
        # Usuário limpa o histórico (apaga os publicados).
        ScheduledPost.objects.filter(status='published').delete()
        r = self.client.get(reverse('analytics:dashboard'))
        # O total no dashboard NÃO caiu (continua 5, não 0).
        self.assertEqual(r.context['publicados_total'], 5)

    def test_total_nunca_menor_que_o_count_vivo(self):
        # Se por algum motivo o contador estiver atrás, usa o count vivo (piso).
        self.acc.publicados_total = 0
        self.acc.save(update_fields=['publicados_total'])
        for _ in range(3):
            ScheduledPost.objects.create(
                owner=self.user, account=self.acc, post_type='REELS',
                status='published', scheduled_for=timezone.now(),
                published_at=timezone.now())
        r = self.client.get(reverse('analytics:dashboard'))
        self.assertEqual(r.context['publicados_total'], 3)

    def test_publicar_incrementa_o_contador(self):
        p = ScheduledPost.objects.create(
            owner=self.user, account=self.acc, post_type='REELS',
            status='queued', scheduled_for=timezone.now(), clean_mode='none')
        p.video_file.name = 'reels/x.mp4'
        p.save()
        with mock.patch('apps.publisher.tasks.InstagramEngine') as Eng, \
             mock.patch('apps.core_utils.garantir_midia_local',
                        return_value=('/tmp/fake.mp4', False)):
            Eng.return_value.publish_meta_api.return_value = {'id': '123'}
            publish_reel(p.id)
        self.acc.refresh_from_db()
        self.assertEqual(self.acc.publicados_total, 6)   # 5 -> 6
        p.refresh_from_db()
        self.assertEqual(p.status, 'published')
