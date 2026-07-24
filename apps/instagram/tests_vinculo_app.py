"""Isolamento de Meta app entre os usuários do site.

Cada usuário do SandraoFlow usa o próprio app Meta. Ninguém pode acabar
usando o app de outro usuário — nem por escolha maliciosa (mandando o id do
app alheio), nem por acidente (um app global do .env servindo a todos).

    python manage.py test apps.instagram.tests_vinculo_app
"""
from django.test import TestCase

from apps.accounts.models import MetaApp, User
from apps.instagram.models import InstagramAccount
from apps.instagram.views import _get_user_meta_app, _meta_credentials, _resolver_app


class IsolamentoEntreUsuariosTest(TestCase):
    """Ninguém usa o app Meta de outro usuário."""

    def setUp(self):
        self.ana = User.objects.create_user(username='ana', password='x', is_active=True)
        self.beto = User.objects.create_user(username='beto', password='x', is_active=True)

        self.app_ana = MetaApp.objects.create(owner=self.ana, name='App da Ana',
                                              meta_app_id='AAA')
        self.app_ana.set_meta_secret('segredo-ana')
        self.app_ana.save()

        self.app_beto = MetaApp.objects.create(owner=self.beto, name='App do Beto',
                                               meta_app_id='BBB')
        self.app_beto.set_meta_secret('segredo-beto')
        self.app_beto.save()

    def test_nao_pega_app_de_outro_usuario_pelo_id(self):
        """Mandar o id do app alheio não dá acesso a ele."""
        self.assertIsNone(_get_user_meta_app(self.ana, self.app_beto.id))

    def test_cada_um_recebe_as_proprias_credenciais(self):
        self.assertEqual(_meta_credentials(self.ana)[0], 'AAA')
        self.assertEqual(_meta_credentials(self.beto)[0], 'BBB')

    def test_app_alheio_passado_a_forca_e_recusado(self):
        app_id, secret = _meta_credentials(self.ana, self.app_beto)
        self.assertEqual((app_id, secret), ('', ''))

    def test_sem_app_proprio_nao_cai_no_app_global(self):
        """O furo antigo: META_APP_ID do .env servia a TODOS os usuários."""
        ze = User.objects.create_user(username='ze', password='x', is_active=True)
        with self.settings(META_APP_ID='APP-DO-SISTEMA', META_APP_SECRET='S'):
            self.assertEqual(_meta_credentials(ze), ('', ''))

    def test_resolver_app_so_enxerga_os_apps_do_proprio_usuario(self):
        # A Ana tem exatamente 1 app: resolve para o dela, nunca para o do Beto.
        self.assertEqual(_resolver_app(self.ana), self.app_ana)
        self.assertEqual(_resolver_app(self.beto), self.app_beto)


class ResolverAppTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dono', password='x', is_active=True)

    def test_com_um_unico_app_resolve_sozinho(self):
        app = MetaApp.objects.create(owner=self.user, name='Unico', meta_app_id='1')
        self.assertEqual(_resolver_app(self.user), app)

    def test_com_varios_apps_nao_adivinha(self):
        """Adivinhar aqui vincularia a conta ao app errado (token inválido)."""
        MetaApp.objects.create(owner=self.user, name='A', meta_app_id='1')
        MetaApp.objects.create(owner=self.user, name='B', meta_app_id='2')
        self.assertIsNone(_resolver_app(self.user))

    def test_escolha_explicita_sempre_vence(self):
        MetaApp.objects.create(owner=self.user, name='A', meta_app_id='1')
        b = MetaApp.objects.create(owner=self.user, name='B', meta_app_id='2')
        self.assertEqual(_resolver_app(self.user, b), b)

    def test_sem_nenhum_app_devolve_nada(self):
        self.assertIsNone(_resolver_app(self.user))


class CredenciaisDaContaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dono', password='x', is_active=True)
        self.app = MetaApp.objects.create(owner=self.user, name='App', meta_app_id='111')
        self.app.set_meta_secret('segredo')
        self.app.save()

    def test_usa_as_credenciais_do_app_da_conta(self):
        conta = InstagramAccount.objects.create(
            owner=self.user, ig_username='c', meta_app=self.app)
        self.assertEqual(conta.credenciais_meta(), ('111', 'segredo'))

    def test_conta_sem_app_falha_em_vez_de_usar_outro(self):
        conta = InstagramAccount.objects.create(owner=self.user, ig_username='c')
        with self.assertRaises(ValueError):
            conta.credenciais_meta()

    def test_app_de_outro_dono_e_recusado(self):
        outro = User.objects.create_user(username='outro', password='x')
        alheio = MetaApp.objects.create(owner=outro, name='Alheio', meta_app_id='999')
        conta = InstagramAccount.objects.create(
            owner=self.user, ig_username='c', meta_app=alheio)
        with self.assertRaises(ValueError):
            conta.credenciais_meta()


class ContaUnicaTest(TestCase):
    """A mesma conta do Instagram não pode existir em dois cadastros."""

    def setUp(self):
        self.a = User.objects.create_user(username='a', password='x', is_active=True)
        self.b = User.objects.create_user(username='b', password='x', is_active=True)
        for u in (self.a, self.b):
            MetaApp.objects.create(owner=u, name='App', meta_app_id='1')
        InstagramAccount.objects.create(owner=self.a, ig_username='alvo', ig_user_id=555)

    def test_outro_usuario_nao_cadastra_a_mesma_conta(self):
        self.client.force_login(self.b)
        resp = self.client.post('/instagram/add-meta/', {
            'ig_username': 'alvo', 'ig_user_id': '555', 'meta_access_token': 'tok',
        })
        self.assertIn('já está cadastrada por outro usuário', resp.content.decode())
        self.assertEqual(InstagramAccount.objects.filter(ig_user_id=555).count(), 1)
