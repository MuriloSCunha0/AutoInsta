"""Restrição temporária da conta (Meta code 25 / 2207050).

Relato do usuário iorio: a conta aparecia "on" e mesmo assim não postava, e o
dono recebia um alerta com o JSON cru da Meta:

    {'message': 'User access is restricted', 'type': 'OAuthException',
     'code': 25, 'error_subcode': 2207050}

Verificado em produção: as contas que davam esse erro respondiam HTTP 200 no
/me e tinham cota longe do teto — token bom, conta viva. É a própria conta que o
IG segurou por um tempo (integridade), não token nem limite de cota.

O tratamento: reconhecer, dar cooldown (não martelar), reagendar sem queimar
retry, marcar a conta como "limitada" (via cooldown) para ela não aparecer como
"on sem publicar", e um alerta que EXPLICA em vez de mostrar o dict cru. A conta
NÃO vira 'error' — o token continua valido.

    python manage.py test apps.publisher.tests_restricao25
"""
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.core_utils import msg_meta_amigavel
from apps.instagram.models import InstagramAccount
from apps.publisher.models import ScheduledPost
from apps.publisher.tasks import (
    _e_app_invalido, _e_rate_limit, _e_restricao_temporaria, publish_reel,
)

MSG_25 = ("Erro ao criar contêiner Meta: {'message': 'User access is restricted', "
          "'type': 'OAuthException', 'code': 25, 'error_subcode': 2207050}")


class DetectorTest(TestCase):
    def test_reconhece_code_25(self):
        self.assertTrue(_e_restricao_temporaria(MSG_25))
        self.assertTrue(_e_restricao_temporaria("User access is restricted"))
        self.assertTrue(_e_restricao_temporaria("erro 2207050 aqui"))

    def test_nao_confunde_com_queda_nem_limite(self):
        # A restricao nao pode cair no ramo de token morto nem no de cota.
        self.assertFalse(_e_app_invalido(MSG_25))
        self.assertFalse(_e_rate_limit(MSG_25))

    def test_190_nao_e_restricao(self):
        m190 = "{'message': 'x', 'type': 'OAuthException', 'code': 190}"
        self.assertFalse(_e_restricao_temporaria(m190))

    def test_mensagem_amigavel_tranquiliza_em_vez_de_assustar(self):
        amigavel = msg_meta_amigavel(MSG_25).lower()
        self.assertIn('restringiu', amigavel)
        self.assertIn('conta está ok', amigavel)   # não é queda
        self.assertNotIn('suspensa', amigavel)      # não manda ver se suspendeu
        self.assertNotIn('oauthexception', amigavel)  # não vaza o JSON cru


class PublishTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='iorio', password='x')
        self.conta = InstagramAccount.objects.create(
            owner=self.user, ig_username='wanessa', status='active',
            meta_access_token='tok', ig_user_id=123)
        self.post = ScheduledPost.objects.create(
            owner=self.user, account=self.conta, post_type='REELS',
            status='processing', scheduled_for=timezone.now())
        self.post.video_file.name = 'reels/x.mp4'
        self.post.save()

    def _publica(self):
        with mock.patch('apps.publisher.tasks.InstagramEngine') as Eng, \
             mock.patch('apps.core_utils.garantir_midia_local',
                        return_value=('/tmp/fake.mp4', False)):
            Eng.return_value.publish_meta_api.side_effect = Exception(MSG_25)
            publish_reel(self.post.id)

    def test_conta_nao_vira_erro(self):
        # O ponto do relato: a conta continua ATIVA (token bom), so restrita.
        self._publica()
        self.conta.refresh_from_db()
        self.assertEqual(self.conta.status, 'active')

    def test_entra_em_cooldown(self):
        self._publica()
        self.conta.refresh_from_db()
        self.assertIsNotNone(self.conta.rate_limited_until)
        self.assertGreater(self.conta.rate_limited_until, timezone.now())

    def test_aparece_como_limitada_na_gestao(self):
        # Via cooldown, motivo_parada passa a explicar — em vez de "on sem postar".
        self._publica()
        self.conta.refresh_from_db()
        motivo = self.conta.motivo_parada
        self.assertIsNotNone(motivo)
        self.assertEqual(motivo[0], 'limitada')

    def test_post_reagendado_sem_queimar_retry(self):
        self._publica()
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, 'queued')
        self.assertEqual(self.post.retry_count, 0)
        self.assertGreater(self.post.scheduled_for, timezone.now())

    def test_mensagem_do_post_nao_e_o_json_cru(self):
        self._publica()
        self.post.refresh_from_db()
        self.assertNotIn('OAuthException', self.post.error_message)
        self.assertIn('restring', self.post.error_message.lower())
