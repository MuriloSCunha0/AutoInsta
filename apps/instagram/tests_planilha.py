"""Planilha de controle de contas — as mesmas colunas da planilha do usuário.

A ficha existe INDEPENDENTE de a conta estar conectada: era justamente isso que
a planilha resolvia (anotar a conta antes/depois de conectar).

Senha, código 2FA e token são credenciais: ficam cifradas com a Fernet do
sistema e NUNCA vão no HTML da página — a tela busca o valor sob clique.

    python manage.py test apps.instagram.tests_planilha
"""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.instagram.models import FichaConta, InstagramAccount

CSV_ORIGINAL = (
    ',,CONTROLE DE CONTAS INSTAGRAM,,,,,TOTAL,RODANDO,PAUSADA,CONTINGENCIA,CAIRAM,C/ RESTRICAO,,\n'
    ',,,,,,,0,0,0,0,0,0,,\n'
    ',,"SANDRAO FLOW - Organize",,,,,,,,,,,,\n'
    ',,,,,,,,,,,,,,\n'
    'ID,@ INSTAGRAM,SENHA,E-MAIL,RESPONSAVEL,STATUS,CONECTADA,CAIU,RESTRICAO,'
    'CONTINGENCIA,2FA,CODIGO 2FA,CODIGO TOKEN,ULTIMO LOGIN,OBSERVACOES\n'
    '001,@fulana,senha123,f@x.com,Ana,Rodando,SIM,,,,SIM,ABCD EFGH,tok123,10/07/2026,teste\n'
    '002,,,,,,,,,,,,,,\n'
    '003,beltrana,outra,b@x.com,Beto,Pausada,,SIM,,,,,,,\n'
)


class ModeloTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d', password='x')
        self.f = FichaConta.objects.create(owner=self.user, ordem=1,
                                           ig_username='fulana')

    def test_credenciais_ficam_cifradas(self):
        self.f.set_senha('minhasenha')
        self.f.save()
        self.f.refresh_from_db()
        # O que está no banco NÃO é o texto puro.
        self.assertNotIn('minhasenha', self.f.senha_enc)
        self.assertEqual(self.f.get_senha(), 'minhasenha')

    def test_seed_2fa_e_normalizado(self):
        # O IG mostra em grupos; guardamos sem espaço e maiúsculo, como no login.
        self.f.set_codigo_2fa('abcd efgh ijkl')
        self.assertEqual(self.f.get_codigo_2fa(), 'ABCDEFGHIJKL')

    def test_campo_vazio_nao_guarda_lixo(self):
        self.f.set_senha('')
        self.assertEqual(self.f.senha_enc, '')
        self.assertEqual(self.f.get_senha(), '')

    def test_situacao_real_so_existe_com_conta_vinculada(self):
        self.assertIsNone(self.f.situacao_real)
        conta = InstagramAccount.objects.create(
            owner=self.user, ig_username='fulana', status='active')
        self.f.conta = conta
        self.assertEqual(self.f.situacao_real, 'no ar')


class TelaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d2', password='x')
        self.client.force_login(self.user)

    def test_abre(self):
        self.assertEqual(self.client.get(reverse('instagram:planilha')).status_code, 200)

    def test_exige_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('instagram:planilha')).status_code, 302)

    def test_credenciais_nao_vao_no_html(self):
        f = FichaConta.objects.create(owner=self.user, ordem=1, ig_username='x')
        f.set_senha('SEGREDO123')
        f.set_codigo_token('TOKEN-SECRETO')
        f.save()
        html = self.client.get(reverse('instagram:planilha')).content.decode()
        self.assertNotIn('SEGREDO123', html)
        self.assertNotIn('TOKEN-SECRETO', html)

    def test_contadores_batem_com_a_planilha(self):
        FichaConta.objects.create(owner=self.user, ordem=1, status='rodando')
        FichaConta.objects.create(owner=self.user, ordem=2, status='pausada')
        FichaConta.objects.create(owner=self.user, ordem=3, caiu=True)
        FichaConta.objects.create(owner=self.user, ordem=4, restricao=True)
        FichaConta.objects.create(owner=self.user, ordem=5, contingencia=True)
        c = self.client.get(reverse('instagram:planilha')).context['contadores']
        self.assertEqual(c['total'], 5)
        self.assertEqual(c['rodando'], 1)
        self.assertEqual(c['pausada'], 1)
        self.assertEqual(c['cairam'], 1)
        self.assertEqual(c['restricao'], 1)
        self.assertEqual(c['contingencia'], 1)


class EdicaoTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d3', password='x')
        self.outro = User.objects.create_user(username='o', password='x')
        self.client.force_login(self.user)
        self.f = FichaConta.objects.create(owner=self.user, ordem=1)

    def _salvar(self, campo, valor, ficha=None):
        return self.client.post(reverse('instagram:planilha_salvar'), {
            'id': (ficha or self.f).id, 'campo': campo, 'valor': valor})

    def test_salva_texto(self):
        self._salvar('responsavel', 'Ana')
        self.f.refresh_from_db()
        self.assertEqual(self.f.responsavel, 'Ana')

    def test_salva_checkbox(self):
        self._salvar('caiu', '1')
        self.f.refresh_from_db()
        self.assertTrue(self.f.caiu)
        self._salvar('caiu', '0')
        self.f.refresh_from_db()
        self.assertFalse(self.f.caiu)

    def test_salva_credencial_cifrada(self):
        self._salvar('senha', 'nova123')
        self.f.refresh_from_db()
        self.assertEqual(self.f.get_senha(), 'nova123')
        self.assertNotIn('nova123', self.f.senha_enc)

    def test_salva_data(self):
        self._salvar('ultimo_login', '2026-07-10')
        self.f.refresh_from_db()
        self.assertEqual(str(self.f.ultimo_login), '2026-07-10')

    def test_status_invalido_e_recusado(self):
        r = self._salvar('status', 'inventado')
        self.assertEqual(r.status_code, 400)

    def test_campo_invalido_e_recusado(self):
        r = self._salvar('owner_id', '999')
        self.assertEqual(r.status_code, 400)

    def test_vincula_a_conta_ao_digitar_o_arroba(self):
        conta = InstagramAccount.objects.create(
            owner=self.user, ig_username='fulana', status='active')
        self._salvar('ig_username', '@fulana')
        self.f.refresh_from_db()
        self.assertEqual(self.f.conta_id, conta.id)
        self.assertTrue(self.f.conectada)

    def test_nao_edita_ficha_de_outro_dono(self):
        alheia = FichaConta.objects.create(owner=self.outro, ordem=1)
        r = self._salvar('responsavel', 'invadido', ficha=alheia)
        self.assertEqual(r.status_code, 404)
        alheia.refresh_from_db()
        self.assertEqual(alheia.responsavel, '')

    def test_revelar_devolve_o_valor(self):
        self.f.set_senha('abc123')
        self.f.save()
        r = self.client.post(reverse('instagram:planilha_revelar'),
                             {'id': self.f.id, 'campo': 'senha'})
        self.assertEqual(r.json()['valor'], 'abc123')

    def test_revelar_nao_vaza_de_outro_dono(self):
        alheia = FichaConta.objects.create(owner=self.outro, ordem=1)
        alheia.set_senha('segredo')
        alheia.save()
        r = self.client.post(reverse('instagram:planilha_revelar'),
                             {'id': alheia.id, 'campo': 'senha'})
        self.assertEqual(r.status_code, 404)


class LinhasTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d4', password='x')
        self.client.force_login(self.user)

    def test_adiciona_linhas(self):
        self.client.post(reverse('instagram:planilha_linhas'), {'quantas': 5})
        self.assertEqual(FichaConta.objects.filter(owner=self.user).count(), 5)

    def test_numeracao_continua_de_onde_parou(self):
        self.client.post(reverse('instagram:planilha_linhas'), {'quantas': 3})
        self.client.post(reverse('instagram:planilha_linhas'), {'quantas': 2})
        ordens = list(FichaConta.objects.filter(owner=self.user)
                      .values_list('ordem', flat=True))
        self.assertEqual(ordens, [1, 2, 3, 4, 5])

    def test_quantidade_absurda_e_limitada(self):
        self.client.post(reverse('instagram:planilha_linhas'), {'quantas': 99999})
        self.assertEqual(FichaConta.objects.filter(owner=self.user).count(), 200)

    def test_exclui(self):
        f = FichaConta.objects.create(owner=self.user, ordem=1)
        self.client.post(reverse('instagram:planilha_excluir', args=[f.id]))
        self.assertFalse(FichaConta.objects.filter(id=f.id).exists())


class SincronizarTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d5', password='x')
        self.client.force_login(self.user)
        self.conta = InstagramAccount.objects.create(
            owner=self.user, ig_username='fulana', status='active')

    def test_traz_conta_conectada(self):
        self.client.post(reverse('instagram:planilha_sincronizar'))
        f = FichaConta.objects.get(owner=self.user)
        self.assertEqual(f.ig_username, 'fulana')
        self.assertTrue(f.conectada)
        self.assertEqual(f.conta_id, self.conta.id)

    def test_nao_apaga_as_anotacoes_do_usuario(self):
        # O ponto: sincronizar atualiza o que o SISTEMA sabe e nunca o que a
        # pessoa digitou.
        f = FichaConta.objects.create(owner=self.user, ordem=1,
                                      ig_username='fulana',
                                      responsavel='Ana', observacoes='chip 41')
        f.set_senha('minhasenha')
        f.save()
        self.client.post(reverse('instagram:planilha_sincronizar'))
        f.refresh_from_db()
        self.assertEqual(f.responsavel, 'Ana')
        self.assertEqual(f.observacoes, 'chip 41')
        self.assertEqual(f.get_senha(), 'minhasenha')

    def test_marca_caiu_quando_a_conta_esta_em_erro(self):
        self.conta.status = 'error'
        self.conta.save()
        self.client.post(reverse('instagram:planilha_sincronizar'))
        self.assertTrue(FichaConta.objects.get(owner=self.user).caiu)

    def test_nao_duplica_ao_sincronizar_duas_vezes(self):
        self.client.post(reverse('instagram:planilha_sincronizar'))
        self.client.post(reverse('instagram:planilha_sincronizar'))
        self.assertEqual(FichaConta.objects.filter(owner=self.user).count(), 1)


class ImportExportTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d6', password='x')
        self.client.force_login(self.user)

    def _importar(self, texto=CSV_ORIGINAL):
        arq = SimpleUploadedFile('p.csv', texto.encode('utf-8'), content_type='text/csv')
        return self.client.post(reverse('instagram:planilha_importar'),
                                {'arquivo': arq}, follow=True)

    def test_importa_pulando_o_cabecalho_decorado(self):
        # O arquivo do Sheets tem titulo e contadores antes do cabecalho real.
        self._importar()
        # 3 linhas de dados, mas a do meio esta vazia (o original vem com 100).
        self.assertEqual(FichaConta.objects.filter(owner=self.user).count(), 2)

    def test_importa_os_campos_certos(self):
        self._importar()
        f = FichaConta.objects.get(owner=self.user, ig_username='fulana')
        self.assertEqual(f.email, 'f@x.com')
        self.assertEqual(f.responsavel, 'Ana')
        self.assertEqual(f.status, 'rodando')
        self.assertTrue(f.conectada)
        self.assertTrue(f.tem_2fa)
        self.assertEqual(f.observacoes, 'teste')
        self.assertEqual(str(f.ultimo_login), '2026-07-10')

    def test_importa_credenciais_cifradas(self):
        self._importar()
        f = FichaConta.objects.get(owner=self.user, ig_username='fulana')
        self.assertEqual(f.get_senha(), 'senha123')
        self.assertEqual(f.get_codigo_2fa(), 'ABCDEFGH')
        self.assertEqual(f.get_codigo_token(), 'tok123')
        self.assertNotIn('senha123', f.senha_enc)

    def test_arroba_no_comeco_e_removido(self):
        self._importar()
        self.assertTrue(FichaConta.objects.filter(ig_username='fulana').exists())

    def test_csv_sem_cabecalho_reclama(self):
        r = self._importar('qualquer,coisa\n1,2\n')
        self.assertContains(r, 'cabeçalho')

    def test_exporta_com_as_colunas_da_planilha(self):
        f = FichaConta.objects.create(owner=self.user, ordem=1, ig_username='fulana')
        f.set_senha('abc')
        f.save()
        r = self.client.get(reverse('instagram:planilha_exportar'))
        self.assertEqual(r.status_code, 200)
        texto = r.content.decode('utf-8-sig')
        self.assertIn('@ INSTAGRAM', texto)
        self.assertIn('CÓDIGO TOKEN', texto)
        self.assertIn('fulana', texto)
        self.assertIn('abc', texto)      # o CSV sai com a senha em texto

    def test_ida_e_volta(self):
        self._importar()
        exportado = self.client.get(reverse('instagram:planilha_exportar')
                                    ).content.decode('utf-8-sig')
        FichaConta.objects.all().delete()
        self._importar(exportado)
        f = FichaConta.objects.get(owner=self.user, ig_username='fulana')
        self.assertEqual(f.get_senha(), 'senha123')
        self.assertEqual(f.responsavel, 'Ana')
        self.assertEqual(f.status, 'rodando')
