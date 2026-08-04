"""Limites de contas e de apps Meta por usuário.

`max_ig_accounts` existia desde o começo, mas NUNCA era aplicado — era só um
campo no admin do Django. Ao passar a valer, o risco era travar todo mundo: em
produção há usuários com 50, 31 e 25 contas contra um default de 3. Por isso
0 = ILIMITADO e a migração 0012 zerou o limite de quem já existia.

    python manage.py test apps.accounts.tests_limites
"""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import MetaApp, User
from apps.instagram.models import InstagramAccount


class LimiteContasTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d', password='x')

    def _conta(self, nome):
        return InstagramAccount.objects.create(
            owner=self.user, ig_username=nome, status='active')

    def test_zero_significa_ilimitado(self):
        self.user.max_ig_accounts = 0
        for i in range(5):
            self._conta(f'c{i}')
        pode, msg = self.user.pode_criar_conta()
        self.assertTrue(pode)
        self.assertEqual(msg, '')

    def test_bloqueia_ao_atingir_o_teto(self):
        self.user.max_ig_accounts = 2
        self._conta('a')
        self.assertTrue(self.user.pode_criar_conta()[0])
        self._conta('b')
        pode, msg = self.user.pode_criar_conta()
        self.assertFalse(pode)
        self.assertIn('2', msg)

    def test_mensagem_diz_quantas_estao_em_uso(self):
        self.user.max_ig_accounts = 1
        self._conta('a')
        _, msg = self.user.pode_criar_conta()
        self.assertIn('1 em uso', msg)

    def test_contagem_bate_com_o_banco(self):
        self._conta('a')
        self._conta('b')
        self.assertEqual(self.user.contas_usadas, 2)


class LimiteAppsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d2', password='x')

    def test_zero_e_ilimitado(self):
        self.user.max_meta_apps = 0
        for i in range(4):
            MetaApp.objects.create(owner=self.user, name=f'app{i}')
        self.assertTrue(self.user.pode_criar_app()[0])

    def test_bloqueia_no_teto(self):
        self.user.max_meta_apps = 1
        MetaApp.objects.create(owner=self.user, name='app1')
        pode, msg = self.user.pode_criar_app()
        self.assertFalse(pode)
        self.assertIn('app', msg.lower())


class EnforcementNaTelaTest(TestCase):
    """O limite tem que valer nos pontos onde a conta REALMENTE nasce."""

    def setUp(self):
        self.user = User.objects.create_user(username='d3', password='x')
        self.user.max_ig_accounts = 1
        self.user.max_meta_apps = 1
        self.user.save()
        self.client.force_login(self.user)

    def test_nao_cria_app_alem_do_limite(self):
        MetaApp.objects.create(owner=self.user, name='primeiro')
        self.client.post(reverse('accounts:add_meta_app'), {
            'name': 'segundo', 'meta_app_id': '123',
        }, follow=True)
        self.assertEqual(MetaApp.objects.filter(owner=self.user).count(), 1)

    def test_cria_ate_o_limite(self):
        self.client.post(reverse('accounts:add_meta_app'), {
            'name': 'primeiro', 'meta_app_id': '123',
        }, follow=True)
        self.assertEqual(MetaApp.objects.filter(owner=self.user).count(), 1)


class GestaoDeUsuarioTest(TestCase):
    """A tela do admin onde o suporte define os limites."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='chefe', password='x', is_staff=True, is_superuser=True)
        self.alvo = User.objects.create_user(username='cliente', password='x')
        self.client.force_login(self.staff)

    def test_abre_a_ficha(self):
        r = self.client.get(reverse('management:user_detail', args=[self.alvo.id]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'cliente')

    def test_salva_os_limites(self):
        self.client.post(reverse('management:user_detail', args=[self.alvo.id]),
                         {'max_ig_accounts': 25, 'max_meta_apps': 3}, follow=True)
        self.alvo.refresh_from_db()
        self.assertEqual(self.alvo.max_ig_accounts, 25)
        self.assertEqual(self.alvo.max_meta_apps, 3)

    def test_valor_invalido_vira_zero(self):
        self.client.post(reverse('management:user_detail', args=[self.alvo.id]),
                         {'max_ig_accounts': 'abc', 'max_meta_apps': -5}, follow=True)
        self.alvo.refresh_from_db()
        self.assertEqual(self.alvo.max_ig_accounts, 0)
        self.assertEqual(self.alvo.max_meta_apps, 0)

    def test_avisa_quando_o_teto_fica_abaixo_do_que_ja_existe(self):
        for i in range(3):
            InstagramAccount.objects.create(
                owner=self.alvo, ig_username=f'c{i}', status='active')
        r = self.client.post(reverse('management:user_detail', args=[self.alvo.id]),
                             {'max_ig_accounts': 1, 'max_meta_apps': 0}, follow=True)
        # As contas atuais continuam existindo — o limite só barra novas.
        self.assertEqual(self.alvo.instagramaccount_set.count(), 3)
        self.assertContains(r, 'ABAIXO')

    def test_usuario_comum_nao_acessa(self):
        self.client.force_login(self.alvo)
        r = self.client.get(reverse('management:user_detail', args=[self.alvo.id]))
        self.assertNotEqual(r.status_code, 200)
