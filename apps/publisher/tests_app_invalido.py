"""App Meta restringido (erro 190) não pode virar tempestade de retries.

O que derrubou tudo de madrugada: a Meta restringiu o app e respondeu
'cannot access the app till you log in to www.instagram.com'. O código tratava
como erro transitório e reagendava a cada 10min — centenas de posts em loop
saturaram o worker. Agora: detecta, marca a conta como caída, põe cooldown
longo e NÃO queima retry.
"""
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.instagram.models import InstagramAccount
from apps.publisher.models import ScheduledPost
from apps.publisher.tasks import _e_app_invalido, publish_reel

MSG_190 = ("Erro ao criar contêiner Meta: {'message': 'Error validating access token: "
           "You cannot access the app till you log in to www.instagram.com and follow "
           "the instructions given.', 'type': 'OAuthException', 'code': 190}")


class DetectorTest(TestCase):
    def test_reconhece_190_e_cannot_access(self):
        self.assertTrue(_e_app_invalido(MSG_190))
        self.assertTrue(_e_app_invalido("OAuthException code 190"))

    def test_nao_confunde_com_rate_limit(self):
        self.assertFalse(_e_app_invalido("Application request limit reached"))
        self.assertFalse(_e_app_invalido("too many actions"))


class AppInvalidoNoPublishTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dono', password='x', is_active=True)
        self.conta = InstagramAccount.objects.create(
            owner=self.user, ig_username='c', status='active',
            meta_access_token='tok', ig_user_id=123)
        self.post = ScheduledPost.objects.create(
            owner=self.user, account=self.conta, post_type='REELS',
            status='processing', scheduled_for=timezone.now())
        # o post precisa de um arquivo "publicável"
        self.post.video_file.name = 'reels/x.mp4'
        self.post.save()

    def _publica_com_erro(self):
        with mock.patch('apps.publisher.tasks.InstagramEngine') as Eng:
            inst = Eng.return_value
            inst.publish_meta_api.side_effect = Exception(MSG_190)
            # evita mexer em disco (limpeza/áudio)
            with mock.patch('os.path.exists', return_value=True), \
                 mock.patch('apps.publisher.tasks.ScheduledPost.video_file',
                            new_callable=mock.PropertyMock, create=True):
                publish_reel(self.post.id)

    def test_conta_vira_erro_e_entra_em_cooldown(self):
        with mock.patch('apps.publisher.tasks.InstagramEngine') as Eng:
            Eng.return_value.publish_meta_api.side_effect = Exception(MSG_190)
            publish_reel(self.post.id)
        self.conta.refresh_from_db()
        self.post.refresh_from_db()
        self.assertEqual(self.conta.status, 'error')
        self.assertIsNotNone(self.conta.rate_limited_until)
        self.assertGreater(self.conta.rate_limited_until, timezone.now())

    def test_nao_queima_retry(self):
        with mock.patch('apps.publisher.tasks.InstagramEngine') as Eng:
            Eng.return_value.publish_meta_api.side_effect = Exception(MSG_190)
            publish_reel(self.post.id)
        self.post.refresh_from_db()
        self.assertEqual(self.post.retry_count, 0)   # não gastou retry
        self.assertEqual(self.post.status, 'queued')  # volta pra fila, não 'failed'
