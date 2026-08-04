"""Abas escondidas por usuário.

Esconder sem bloquear seria teatro: o link some do menu, mas quem digitar
/library/downloader/ entra assim mesmo. Por isso o registro de abas é ÚNICO
(apps/accounts/abas.py) e serve ao menu, ao middleware e à tela de gestão —
os três não podem divergir.

    python manage.py test apps.accounts.tests_abas
"""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.abas import ABAS, aba_da_rota, limpar, por_grupo
from apps.accounts.models import User


class RegistroTest(TestCase):
    def test_toda_aba_tem_rotulo_grupo_e_rotas(self):
        for chave, (rotulo, grupo, rotas) in ABAS.items():
            with self.subTest(chave):
                self.assertTrue(rotulo)
                self.assertTrue(grupo)
                self.assertTrue(rotas)

    def test_rota_conhecida_encontra_a_aba(self):
        self.assertEqual(aba_da_rota('library', 'downloader'), 'downloader')
        self.assertEqual(aba_da_rota('publisher', 'composer'), 'composer')
        self.assertEqual(aba_da_rota('instagram', 'gestao'), 'gestao_contas')

    def test_namespace_inteiro_casa(self):
        # A pressel tem varias rotas; todas pertencem a mesma aba.
        self.assertEqual(aba_da_rota('pressel', 'lista'), 'pressel')
        self.assertEqual(aba_da_rota('pressel', 'baixar'), 'pressel')

    def test_rota_de_fora_nao_pertence_a_aba_nenhuma(self):
        self.assertIsNone(aba_da_rota('accounts', 'settings'))
        self.assertIsNone(aba_da_rota('inventado', 'qualquer'))

    def test_limpar_descarta_chave_invalida(self):
        self.assertEqual(limpar(['cta', 'nao-existe', '']), ['cta'])

    def test_configuracoes_nunca_pode_ser_escondida(self):
        self.assertEqual(limpar(['settings']), [])

    def test_por_grupo_cobre_todas_as_abas(self):
        total = sum(len(itens) for _g, itens in por_grupo())
        self.assertEqual(total, len(ABAS))


class ModeloTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d', password='x')

    def test_sem_nada_escondido_ve_tudo(self):
        self.assertEqual(self.user.abas_ocultas_set, set())
        self.assertTrue(self.user.pode_ver_aba('cta'))

    def test_esconde_e_persiste(self):
        self.user.set_abas_ocultas(['cta', 'downloader'])
        self.user.save()
        self.user.refresh_from_db()
        self.assertEqual(self.user.abas_ocultas_set, {'cta', 'downloader'})
        self.assertFalse(self.user.pode_ver_aba('cta'))
        self.assertTrue(self.user.pode_ver_aba('pressel'))

    def test_staff_enxerga_tudo_mesmo_com_abas_marcadas(self):
        # Evita o tiro no pe de um admin se trancar para fora.
        self.user.is_staff = True
        self.user.set_abas_ocultas(['cta', 'downloader'])
        self.user.save()
        self.assertEqual(self.user.abas_ocultas_set, set())


class MiddlewareTest(TestCase):
    """A aba escondida tem que BLOQUEAR o acesso direto pela URL."""

    def setUp(self):
        self.user = User.objects.create_user(username='c', password='x')
        self.client.force_login(self.user)

    def test_acessa_quando_liberado(self):
        self.assertEqual(self.client.get(reverse('library:cta')).status_code, 200)

    def test_bloqueia_quando_escondido(self):
        self.user.set_abas_ocultas(['cta'])
        self.user.save()
        r = self.client.get(reverse('library:cta'))
        self.assertEqual(r.status_code, 302)

    def test_bloqueia_o_namespace_inteiro(self):
        self.user.set_abas_ocultas(['pressel'])
        self.user.save()
        self.assertEqual(self.client.get(reverse('pressel:lista')).status_code, 302)
        self.assertEqual(self.client.get(reverse('pressel:nova')).status_code, 302)

    def test_nao_bloqueia_as_outras_abas(self):
        self.user.set_abas_ocultas(['cta'])
        self.user.save()
        self.assertEqual(self.client.get(reverse('library:media')).status_code, 200)

    def test_staff_passa_mesmo_marcado(self):
        self.user.is_staff = True
        self.user.set_abas_ocultas(['cta'])
        self.user.save()
        self.assertEqual(self.client.get(reverse('library:cta')).status_code, 200)


class MenuTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='m', password='x')
        self.client.force_login(self.user)

    def test_link_some_do_menu(self):
        r = self.client.get(reverse('library:media'))
        self.assertContains(r, 'Gerador de CTA')
        self.user.set_abas_ocultas(['cta'])
        self.user.save()
        r = self.client.get(reverse('library:media'))
        self.assertNotContains(r, 'Gerador de CTA')

    def test_configuracoes_continua_no_menu(self):
        self.user.set_abas_ocultas(list(ABAS))
        self.user.save()
        r = self.client.get(reverse('accounts:settings'))
        self.assertContains(r, 'Configurações')


class GestaoTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='chefe', password='x', is_staff=True, is_superuser=True)
        self.alvo = User.objects.create_user(username='cliente', password='x')
        self.client.force_login(self.staff)

    def test_lista_de_usuarios_leva_para_a_ficha(self):
        # A tela existia mas nao tinha link nenhum apontando para ela — o
        # usuario nao achava onde configurar limites e abas.
        r = self.client.get(reverse('management:users'))
        self.assertContains(r, reverse('management:user_detail', args=[self.alvo.id]))

    def test_tela_lista_as_abas(self):
        r = self.client.get(reverse('management:user_detail', args=[self.alvo.id]))
        self.assertContains(r, 'Abas escondidas')
        self.assertContains(r, 'aba-cta')

    def test_salva_as_abas_marcadas(self):
        self.client.post(reverse('management:user_detail', args=[self.alvo.id]),
                         {'max_ig_accounts': 0, 'max_meta_apps': 0,
                          'abas_ocultas': ['cta', 'downloader']}, follow=True)
        self.alvo.refresh_from_db()
        self.assertEqual(self.alvo.abas_ocultas_set, {'cta', 'downloader'})

    def test_desmarcar_libera_de_novo(self):
        self.alvo.set_abas_ocultas(['cta'])
        self.alvo.save()
        self.client.post(reverse('management:user_detail', args=[self.alvo.id]),
                         {'max_ig_accounts': 0, 'max_meta_apps': 0}, follow=True)
        self.alvo.refresh_from_db()
        self.assertEqual(self.alvo.abas_ocultas_set, set())

    def test_chave_inventada_e_ignorada(self):
        self.client.post(reverse('management:user_detail', args=[self.alvo.id]),
                         {'max_ig_accounts': 0, 'max_meta_apps': 0,
                          'abas_ocultas': ['cta', 'hackeado']}, follow=True)
        self.alvo.refresh_from_db()
        self.assertEqual(self.alvo.abas_ocultas_set, {'cta'})
