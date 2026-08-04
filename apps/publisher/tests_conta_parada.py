"""Conta parada tem que APARECER como parada — e não travar post em 'processing'.

Relato do usuário (04/08/2026): "o post agendado desde as 15:35 não saiu" — às
15:56 ele ainda aparecia como PUBLICANDO. A conta (@clara.oliveira5y) tinha ido
DE MOLHO (a Meta limitou 2x seguidas → pausada, fila reagendada para amanhã),
mas o post que disparou o de-molho ficava em `processing` e só voltava para a
fila pela rede de segurança de 15 min. Na tela parecia fila travada.

Dois consertos travados aqui:
  1. o post volta para 'queued' na hora em que a conta vai de molho;
  2. `motivo_parada` diz POR QUE a conta não está publicando, para a fila
     mostrar "de molho"/"pausada" em vez de um "na fila" eterno.

    python manage.py test apps.publisher.tests_conta_parada
"""
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.instagram.models import InstagramAccount
from apps.publisher.models import ScheduledPost
from apps.publisher.tasks import publish_reel

MSG_LIMITE = ("Erro ao criar contêiner Meta: {'message': '(#9) The user has "
              "reached the maximum number of posts', 'type': 'OAuthException', "
              "'code': 9}")


class MotivoParadaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d', password='x')
        self.acc = InstagramAccount.objects.create(
            owner=self.user, ig_username='c', status='active',
            meta_access_token='t', ig_user_id=1)

    def test_conta_saudavel_nao_tem_motivo(self):
        self.assertIsNone(self.acc.motivo_parada)

    def test_pausada_pelo_usuario(self):
        self.acc.pausada = True
        self.assertEqual(self.acc.motivo_parada[0], 'pausada')
        self.assertFalse(self.acc.de_molho)

    def test_de_molho_e_diferente_de_pausada(self):
        self.acc.pausada = True
        self.acc.meta_limit_count = 2
        self.assertTrue(self.acc.de_molho)
        self.assertEqual(self.acc.motivo_parada[0], 'de molho')

    def test_conta_caida(self):
        self.acc.status = 'error'
        self.assertEqual(self.acc.motivo_parada[0], 'conta caiu')

    def test_explicacao_diz_que_a_fila_nao_travou(self):
        self.acc.pausada = True
        self.assertIn('não está travada', self.acc.motivo_parada[1])


class DeMolhoNaoDeixaPostPresoTest(TestCase):
    """O post que dispara o de-molho não pode ficar 'publicando...' para sempre."""

    def setUp(self):
        self.user = User.objects.create_user(username='d2', password='x')
        self.acc = InstagramAccount.objects.create(
            owner=self.user, ig_username='c2', status='active',
            meta_access_token='t', ig_user_id=2,
            meta_limit_count=1)          # já levou 1 limite: o próximo põe de molho
        self.post = ScheduledPost.objects.create(
            owner=self.user, account=self.acc, post_type='REELS',
            status='processing', processing_since=timezone.now(),
            scheduled_for=timezone.now())
        self.post.video_file.name = 'reels/x.mp4'
        self.post.save()

    def _publica_com_limite(self):
        # `garantir_midia_local` baixaria o arquivo da URL pública (SITE_URL).
        # Sem o mock o teste tentaria a rede e falharia ANTES de chegar na Meta
        # — que é justamente o que queremos exercitar aqui.
        with mock.patch('apps.publisher.tasks.InstagramEngine') as Eng, \
             mock.patch('apps.core_utils.garantir_midia_local',
                        return_value=('/tmp/fake.mp4', False)):
            Eng.return_value.publish_meta_api.side_effect = Exception(MSG_LIMITE)
            publish_reel(self.post.id)

    def test_conta_vai_de_molho(self):
        self._publica_com_limite()
        self.acc.refresh_from_db()
        self.assertTrue(self.acc.pausada)
        self.assertTrue(self.acc.de_molho)

    def test_post_sai_de_processing(self):
        self._publica_com_limite()
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, 'queued')
        self.assertIsNone(self.post.processing_since)

    def test_post_explica_o_motivo(self):
        self._publica_com_limite()
        self.post.refresh_from_db()
        self.assertIn('molho', (self.post.error_message or '').lower())

    def test_nao_queima_retry(self):
        self._publica_com_limite()
        self.post.refresh_from_db()
        self.assertEqual(self.post.retry_count, 0)
