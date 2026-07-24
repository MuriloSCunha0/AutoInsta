"""Alertas, apelido no ranking e modo forçado.

    python manage.py test apps.notifications.tests_alertas
"""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.instagram.models import InstagramAccount
from apps.notifications.alertas import alertar, preferencias
from apps.notifications.models import Notification
from apps.publisher.models import ScheduledPost


class AlertaTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='dono', password='x', is_active=True)

    def test_respeita_o_desligado(self):
        pref = preferencias(self.user)
        pref.conta_caiu = False
        pref.save()
        self.assertFalse(alertar(self.user, 'conta_caiu', 'T', 'M', chave='k'))
        self.assertEqual(Notification.objects.count(), 0)

    def test_envia_quando_ligado(self):
        self.assertTrue(alertar(self.user, 'conta_caiu', 'Caiu', 'msg', chave='k'))
        self.assertEqual(Notification.objects.count(), 1)

    def test_nao_repete_o_mesmo_alerta(self):
        """Sem isto o usuário levaria o mesmo aviso a cada rodada e silenciaria tudo."""
        alertar(self.user, 'conta_caiu', 'Caiu', 'msg', chave='mesma')
        alertar(self.user, 'conta_caiu', 'Caiu', 'msg', chave='mesma')
        alertar(self.user, 'conta_caiu', 'Caiu', 'msg', chave='mesma')
        self.assertEqual(Notification.objects.count(), 1)

    def test_alertas_diferentes_passam(self):
        alertar(self.user, 'conta_caiu', 'A', 'm', chave='conta:1')
        alertar(self.user, 'conta_caiu', 'B', 'm', chave='conta:2')
        self.assertEqual(Notification.objects.count(), 2)

    def test_um_usuario_nao_recebe_alerta_do_outro(self):
        outro = User.objects.create_user(username='outro', password='x', is_active=True)
        alertar(self.user, 'conta_caiu', 'A', 'm', chave='k')
        self.assertEqual(Notification.objects.filter(user=outro).count(), 0)


class ContaCaiuTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='dono', password='x', is_active=True)

    def test_avisa_quando_a_conta_perde_permissao(self):
        InstagramAccount.objects.create(owner=self.user, ig_username='caiu',
                                        status='error', last_error='token revogado')
        from apps.notifications.tasks import checar_alertas
        checar_alertas()
        n = Notification.objects.filter(user=self.user).first()
        self.assertIsNotNone(n)
        self.assertIn('caiu', n.message)

    def test_conta_saudavel_nao_gera_alerta(self):
        InstagramAccount.objects.create(owner=self.user, ig_username='ok', status='active')
        from apps.notifications.tasks import checar_alertas
        checar_alertas()
        self.assertEqual(Notification.objects.filter(title='Conta desconectada').count(), 0)


class TelegramTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='dono', password='x', is_active=True)

    def test_token_e_guardado_criptografado(self):
        pref = preferencias(self.user)
        pref.set_telegram_token('123456:SEGREDO')
        pref.save()
        self.assertNotIn('SEGREDO', pref.telegram_token_enc)
        self.assertEqual(pref.get_telegram_token(), '123456:SEGREDO')

    def test_envia_ao_telegram_quando_configurado(self):
        pref = preferencias(self.user)
        pref.telegram_chat_id = '999'
        pref.set_telegram_token('tok')
        pref.save()
        with mock.patch('requests.post') as post:
            post.return_value = mock.Mock(status_code=200)
            alertar(self.user, 'conta_caiu', 'Caiu', 'msg', chave='k')
        self.assertEqual(post.call_args.kwargs['data']['chat_id'], '999')

    def test_salvar_sem_digitar_token_nao_apaga_o_guardado(self):
        """O mesmo erro que apagava o apelido: campo ausente não pode zerar."""
        pref = preferencias(self.user)
        pref.set_telegram_token('tok-original')
        pref.save()
        self.client.force_login(self.user)
        self.client.post(reverse('notifications:alertas_salvar'),
                         {'telegram_chat_id': '999', 'telegram_token': ''})
        pref.refresh_from_db()
        self.assertEqual(pref.get_telegram_token(), 'tok-original')


class ApelidoTest(TestCase):
    """O ranking mostra o APELIDO, e salvar o perfil não pode apagá-lo."""

    def setUp(self):
        self.user = User.objects.create_user(username='dono', password='x', is_active=True)
        self.client.force_login(self.user)

    def test_salvar_perfil_sem_o_campo_nao_apaga_o_apelido(self):
        self.user.nickname = 'Sandrao do Hot'
        self.user.save()
        self.client.post(reverse('accounts:profile_update'),
                         {'action': 'update_details', 'first_name': 'Jose'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.nickname, 'Sandrao do Hot')
        self.assertEqual(self.user.first_name, 'Jose')

    def test_ranking_usa_o_apelido_e_nao_o_nome(self):
        self.user.nickname = 'Apelido'
        self.user.first_name = 'Nome Real'
        self.user.save()
        conta = InstagramAccount.objects.create(owner=self.user, ig_username='c')
        agora = timezone.now()
        ScheduledPost.objects.create(owner=self.user, account=conta, post_type='REELS',
                                     status='published', scheduled_for=agora,
                                     published_at=agora)
        resp = self.client.get(reverse('analytics:dashboard'))
        nomes = [i['name'] for i in resp.context['ranking_list']]
        self.assertIn('Apelido', nomes)
        self.assertNotIn('Nome Real', nomes)

    def test_sem_apelido_cai_no_usuario_e_nao_no_nome_real(self):
        self.user.first_name = 'Nome Real'
        self.user.save()
        conta = InstagramAccount.objects.create(owner=self.user, ig_username='c')
        agora = timezone.now()
        ScheduledPost.objects.create(owner=self.user, account=conta, post_type='REELS',
                                     status='published', scheduled_for=agora,
                                     published_at=agora)
        resp = self.client.get(reverse('analytics:dashboard'))
        nomes = [i['name'] for i in resp.context['ranking_list']]
        self.assertIn('dono', nomes)
        self.assertNotIn('Nome Real', nomes)


class ForcarPostagemTest(TestCase):
    """Modo forçado: publica mesmo com teto batido ou em cooldown."""

    def setUp(self):
        self.user = User.objects.create_user(username='dono', password='x', is_active=True)
        self.conta = InstagramAccount.objects.create(
            owner=self.user, ig_username='c', daily_post_limit=1)
        agora = timezone.now()
        # já publicou hoje = teto batido
        ScheduledPost.objects.create(owner=self.user, account=self.conta, post_type='REELS',
                                     status='published', scheduled_for=agora,
                                     published_at=agora)
        self.pendente = ScheduledPost.objects.create(
            owner=self.user, account=self.conta, post_type='REELS',
            status='queued', scheduled_for=agora)

    def _rodar(self):
        with mock.patch('apps.publisher.tasks.publish_reel.delay') as d:
            from apps.publisher.tasks import process_scheduled_posts
            process_scheduled_posts()
            return d.called

    def test_sem_forcar_o_teto_segura(self):
        self.assertFalse(self._rodar())

    def test_com_forcar_publica_mesmo_no_teto(self):
        self.conta.ignorar_limites = True
        self.conta.save()
        self.assertTrue(self._rodar())

    def test_com_forcar_ignora_o_cooldown(self):
        self.conta.ignorar_limites = True
        self.conta.daily_post_limit = 0
        self.conta.rate_limited_until = timezone.now() + timezone.timedelta(hours=3)
        self.conta.save()
        self.assertTrue(self._rodar())

    def test_botao_liga_e_limpa_o_cooldown(self):
        self.conta.rate_limited_until = timezone.now() + timezone.timedelta(hours=3)
        self.conta.save()
        self.client.force_login(self.user)
        self.client.post(reverse('instagram:toggle_forcar', args=[self.conta.id]))
        self.conta.refresh_from_db()
        self.assertTrue(self.conta.ignorar_limites)
        self.assertIsNone(self.conta.rate_limited_until)
