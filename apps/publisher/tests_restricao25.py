"""Restrição da conta (Meta code 25 / 2207050) — a ESCADA.

O que a Meta manda:

    {'message': 'User access is restricted', 'type': 'OAuthException',
     'code': 25, 'error_subcode': 2207050}

Verificado em produção: as contas que davam esse erro respondiam HTTP 200 no
/me e tinham cota longe do teto — token bom, conta viva.

O QUE MUDOU (queixa do usuário iorio, conta @debora_wachholz7525): a conta dele
está restrita SÓ PARA MENSAGENS e posta normalmente, mas a Graph devolvia esse
mesmo code 25 no /media. O tratamento antigo concluía "restrição de POSTAGEM" na
1ª recusa: cooldown de 3h, card "Limitada pela Meta" — e, pior, REGRAVAVA o
cooldown (agora+3h) a cada nova recusa, então a contagem regressiva reiniciava e
a conta nunca voltava. A Meta NÃO expõe o tipo de restrição pela API; a única
forma de saber é tentar publicar.

O tratamento novo é uma escada com prazo FIXO em cada degrau:

  1..2 recusas  — nem marca a conta: tenta de novo no horário normal da fila.
                  Se publicar, a série zera e o usuário nem fica sabendo.
  3ª recusa     — "restrita": cooldown de 3h com hora certa (não se renova).
  4ª+           — de molho: descanso longo + fila reagendada (igual ao limite).

    python manage.py test apps.publisher.tests_restricao25
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.core_utils import msg_meta_amigavel
from apps.instagram.models import InstagramAccount
from apps.publisher.models import ScheduledPost
from apps.publisher.tasks import (
    _deve_revezar, _e_app_invalido, _e_rate_limit, _e_restricao_temporaria,
    publish_reel,
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

    def test_reveza_para_a_engine(self):
        # Unico teste empirico do TIPO de restricao: se a sessao publica, nao era
        # restricao de postagem. Falha limpa no /media -> nao duplica post.
        self.assertTrue(_deve_revezar(MSG_25))

    def test_mensagem_amigavel_nao_afirma_bloqueio_de_post(self):
        amigavel = msg_meta_amigavel(MSG_25).lower()
        self.assertIn('restrita', amigavel)
        self.assertIn('mensagens', amigavel)     # diz que pode ser de outro tipo
        self.assertNotIn('suspensa', amigavel)   # nao manda ver se suspendeu
        self.assertNotIn('oauthexception', amigavel)   # nao vaza o JSON cru


class EscadaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='iorio', password='x')
        self.conta = InstagramAccount.objects.create(
            owner=self.user, ig_username='debora', status='active',
            meta_access_token='tok', ig_user_id=123)
        self.post = ScheduledPost.objects.create(
            owner=self.user, account=self.conta, post_type='REELS',
            status='processing', scheduled_for=timezone.now())
        self.post.video_file.name = 'reels/x.mp4'
        self.post.save()

    def _publica(self, sonda=''):
        """Uma tentativa que a Meta recusa com code 25.

        A sonda de leitura vai à rede de verdade em produção — aqui ela é
        dublada (o teste não pode depender da Graph).
        """
        self.post.status = 'processing'
        self.post.save(update_fields=['status'])
        with mock.patch('apps.publisher.tasks.InstagramEngine') as Eng, \
             mock.patch('apps.publisher.tasks._sondar_restricao', return_value=sonda), \
             mock.patch('apps.core_utils.garantir_midia_local',
                        return_value=('/tmp/fake.mp4', False)):
            Eng.return_value.publish_meta_api.side_effect = Exception(MSG_25)
            publish_reel(self.post.id)
        self.conta.refresh_from_db()
        self.post.refresh_from_db()

    def _recusar(self, vezes):
        for _ in range(vezes):
            # Cada tentativa e' um ciclo novo: o cooldown do ciclo anterior ja'
            # venceu (e' assim que a fila redespacha).
            if self.conta.rate_limited_until:
                self.conta.rate_limited_until = timezone.now() - timedelta(minutes=1)
                self.conta.save(update_fields=['rate_limited_until'])
            self._publica()

    # ── degraus 1 e 2: a conta NAO e' marcada ────────────────────────────────
    def test_primeira_recusa_nao_marca_a_conta(self):
        self._publica()
        self.assertIsNone(self.conta.rate_limited_until)
        self.assertFalse(self.conta.em_cooldown)
        self.assertIsNone(self.conta.motivo_parada)   # segue "no ar"
        self.assertEqual(self.conta.restricao_count, 1)

    def test_conta_nao_vira_erro(self):
        self._recusar(4)
        self.assertEqual(self.conta.status, 'active')   # token continua bom

    def test_retenta_no_ritmo_da_fila_sem_queimar_retry(self):
        self.post.interval_minutes = 30
        self.post.save(update_fields=['interval_minutes'])
        self._publica()
        self.assertEqual(self.post.status, 'queued')
        self.assertEqual(self.post.retry_count, 0)
        espera = self.post.scheduled_for - timezone.now()
        self.assertGreater(espera, timedelta(minutes=25))
        self.assertLess(espera, timedelta(minutes=35))

    def test_publicar_no_meio_zera_a_serie(self):
        # E' o caso da restricao so' de MENSAGENS: a conta posta, entao a serie
        # nunca chega ao degrau que marca "restrita".
        self._publica()
        self.assertEqual(self.conta.restricao_count, 1)
        with mock.patch('apps.publisher.tasks.InstagramEngine') as Eng, \
             mock.patch('apps.core_utils.garantir_midia_local',
                        return_value=('/tmp/fake.mp4', False)):
            Eng.return_value.publish_meta_api.return_value = {'id': '999'}
            self.post.status = 'processing'
            self.post.save(update_fields=['status'])
            publish_reel(self.post.id)
        self.conta.refresh_from_db()
        self.assertEqual(self.conta.restricao_count, 0)
        self.assertIsNone(self.conta.restricao_desde)

    # ── degrau 3: restrita, com prazo FIXO ───────────────────────────────────
    def test_terceira_recusa_marca_restrita(self):
        self._recusar(3)
        self.assertTrue(self.conta.em_cooldown)
        self.assertTrue(self.conta.restrita)
        self.assertFalse(self.conta.de_molho)
        rotulo, _ = self.conta.motivo_parada
        self.assertEqual(rotulo, 'restrita')     # nao "limitada"

    def test_prazo_nao_reinicia_enquanto_esta_correndo(self):
        # O bug relatado: cada nova recusa regravava agora+3h e a contagem
        # regressiva nunca chegava a zero.
        self._recusar(3)
        prazo = self.conta.rate_limited_until
        self._publica()          # nova recusa AINDA dentro do cooldown
        self.assertEqual(self.conta.rate_limited_until, prazo)

    def test_sonda_entra_no_aviso_da_conta(self):
        # A Meta nao diz o TIPO da restricao; a sonda compara o que responde
        # (perfil) com o que e' recusado (publicar) e isso vai para o card.
        self._recusar(2)
        self._publica(sonda='Conferimos: o perfil responde normalmente.')
        self.assertIn('Conferimos', self.conta.last_error)

    def test_sonda_so_roda_na_virada_para_restrita(self):
        # 1x por episodio: chamar a Graph a cada recusa e' o padrao que agrava a
        # punicao (ja' derrubou contas em cascata neste projeto).
        with mock.patch('apps.publisher.tasks.InstagramEngine') as Eng, \
             mock.patch('apps.publisher.tasks._sondar_restricao', return_value='') as sonda, \
             mock.patch('apps.core_utils.garantir_midia_local',
                        return_value=('/tmp/fake.mp4', False)):
            Eng.return_value.publish_meta_api.side_effect = Exception(MSG_25)
            for _ in range(4):
                self.conta.refresh_from_db()
                if self.conta.rate_limited_until:
                    self.conta.rate_limited_until = timezone.now() - timedelta(minutes=1)
                    self.conta.save(update_fields=['rate_limited_until'])
                self.post.refresh_from_db()
                self.post.status = 'processing'
                self.post.save(update_fields=['status'])
                publish_reel(self.post.id)
        self.assertEqual(sonda.call_count, 1)

    def test_forcar_e_desligado(self):
        self.conta.ignorar_limites = True
        self.conta.save(update_fields=['ignorar_limites'])
        self._publica()
        self.assertFalse(self.conta.ignorar_limites)

    # ── degrau 4: de molho ───────────────────────────────────────────────────
    def test_quarta_recusa_poe_de_molho(self):
        self._recusar(4)
        self.assertTrue(self.conta.de_molho)
        rotulo, texto = self.conta.motivo_parada
        self.assertEqual(rotulo, 'de molho')
        self.assertIn('restrita', texto)
        # De molho NAO e' pausa eterna: volta a tentar sozinha.
        self.assertFalse(self.conta.pausada)
        self.assertGreater(self.conta.rate_limited_until, timezone.now())

    def test_mensagem_do_post_nao_e_o_json_cru(self):
        self._publica()
        self.assertNotIn('OAuthException', self.post.error_message)
        self.assertIn('restrita', self.post.error_message.lower())
