"""Observabilidade por usuário no admin — feed de publicações em tempo real.

Mostra ao suporte o que sai certo e o que falha, com o erro CRU + tradução
humana + ação. Fonte: ScheduledPost (sem camada de log nova).

    python manage.py test apps.management.tests_observabilidade
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from apps.accounts.models import User
from apps.instagram.models import InstagramAccount
from apps.publisher.models import ScheduledPost
from apps.publisher.tasks import diagnosticar_erro

MSG_190 = "{'message': 'Error validating access token', 'type': 'OAuthException', 'code': 190}"
MSG_25 = "{'message': 'User access is restricted', 'type': 'OAuthException', 'code': 25, 'error_subcode': 2207050}"
# Mensagens que o PRÓPRIO sistema grava em error_message (a maioria real).
MSG_EXPIRADO = ('Não publicado: o horário passou há mais de 6h (a conta estava '
                'fora). Evitamos subir a fila antiga de uma vez. Reenvie se ainda '
                'quiser publicar.')
MSG_LEGENDA = "Erro ao criar contêiner Meta: {'error': {'message': 'The caption was too long.', 'code': 100}}"
MSG_REDE = ("HTTPSConnectionPool(host='graph.instagram.com', port=443): Max "
            "retries exceeded with url: /v23.0/1784/media")
MSG_METODO = "Erro ao criar contêiner Meta: {'error': {'message': 'Unsupported request - method type: post'}}"


class DiagnosticoTest(TestCase):
    def test_traduz_190_para_reconectar(self):
        d = diagnosticar_erro(MSG_190)
        self.assertEqual(d['categoria'], 'token')
        self.assertIn('reconect', d['titulo'].lower())
        self.assertIn('instagram.com', d['acao'].lower())
        self.assertEqual(d['cor'], 'danger')

    def test_traduz_code_25_como_temporario(self):
        d = diagnosticar_erro(MSG_25)
        self.assertEqual(d['categoria'], 'restricao')
        self.assertIn('sozinha', d['acao'].lower())

    def test_expirado_conta_fora(self):
        # O erro nº1 em produção (80% dos casos): tem de sair do "não catalogado".
        d = diagnosticar_erro(MSG_EXPIRADO)
        self.assertEqual(d['categoria'], 'expirado')
        self.assertIn('fora', d['titulo'].lower())
        self.assertIn('agendar de novo', d['acao'].lower())

    def test_legenda_longa(self):
        d = diagnosticar_erro(MSG_LEGENDA)
        self.assertEqual(d['categoria'], 'legenda')
        self.assertIn('encurte', d['acao'].lower())

    def test_falha_de_rede(self):
        d = diagnosticar_erro(MSG_REDE)
        self.assertEqual(d['categoria'], 'rede')

    def test_conta_nao_profissional(self):
        d = diagnosticar_erro(MSG_METODO)
        self.assertEqual(d['categoria'], 'permissao')
        self.assertIn('profissional', d['explicacao'].lower())

    def test_erro_vazio_nao_diagnostica(self):
        self.assertIsNone(diagnosticar_erro(''))
        self.assertIsNone(diagnosticar_erro(None))

    def test_erro_desconhecido_cai_em_outro(self):
        d = diagnosticar_erro('algo totalmente novo aqui')
        self.assertEqual(d['categoria'], 'outro')


class FeedTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='chefe', password='x', is_staff=True, is_superuser=True)
        self.alvo = User.objects.create_user(username='cliente', password='x')
        self.outro = User.objects.create_user(username='outro', password='x')
        self.client.force_login(self.staff)
        self.conta = InstagramAccount.objects.create(
            owner=self.alvo, ig_username='c', status='active')
        self.now = timezone.now()

    def _url(self):
        return reverse('management:user_obs', args=[self.alvo.id])

    def test_abre(self):
        self.assertEqual(self.client.get(self._url()).status_code, 200)

    def test_mostra_sucesso(self):
        ScheduledPost.objects.create(
            owner=self.alvo, account=self.conta, post_type='REELS',
            status='published', published_at=self.now - timedelta(minutes=2),
            scheduled_for=self.now)
        r = self.client.get(self._url())
        self.assertContains(r, 'Publicado')
        self.assertContains(r, '@c')

    def test_mostra_erro_com_diagnostico(self):
        ScheduledPost.objects.create(
            owner=self.alvo, account=self.conta, post_type='REELS',
            status='failed', error_message=MSG_190,
            processing_since=self.now - timedelta(minutes=1),
            scheduled_for=self.now)
        r = self.client.get(self._url())
        # O erro cru NÃO aparece solto; a tradução clara aparece.
        self.assertContains(r, 'reconectar')
        self.assertContains(r, 'instagram.com')

    def test_contadores_batem(self):
        for _ in range(3):
            ScheduledPost.objects.create(
                owner=self.alvo, account=self.conta, post_type='REELS',
                status='published', published_at=self.now - timedelta(minutes=1),
                scheduled_for=self.now)
        ScheduledPost.objects.create(
            owner=self.alvo, account=self.conta, post_type='REELS',
            status='failed', error_message=MSG_25,
            processing_since=self.now - timedelta(minutes=1), scheduled_for=self.now)
        r = self.client.get(self._url())
        self.assertEqual(r.context['resumo']['ok'], 3)
        self.assertEqual(r.context['resumo']['erros'], 1)

    def test_so_eventos_do_alvo(self):
        conta_outro = InstagramAccount.objects.create(
            owner=self.outro, ig_username='alheia', status='active')
        ScheduledPost.objects.create(
            owner=self.outro, account=conta_outro, post_type='REELS',
            status='published', published_at=self.now, scheduled_for=self.now)
        r = self.client.get(self._url())
        self.assertNotContains(r, '@alheia')

    def test_evento_antigo_nao_aparece(self):
        ScheduledPost.objects.create(
            owner=self.alvo, account=self.conta, post_type='REELS',
            status='published', published_at=self.now - timedelta(hours=5),
            scheduled_for=self.now)
        self.assertEqual(len(self.client.get(self._url()).context['eventos']), 0)

    def test_usuario_comum_nao_acessa(self):
        self.client.force_login(self.alvo)
        r = self.client.get(self._url())
        self.assertNotEqual(r.status_code, 200)

    def test_ficha_tem_a_secao_de_observabilidade(self):
        r = self.client.get(reverse('management:user_detail', args=[self.alvo.id]))
        self.assertContains(r, 'Observabilidade')
        self.assertContains(r, reverse('management:user_obs', args=[self.alvo.id]))
