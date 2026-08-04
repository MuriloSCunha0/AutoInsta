"""Construtor de pressel: a página sai igual ao modelo e o HTML anda sozinho.

O ponto crítico é o EXPORT: o usuário baixa um arquivo e joga no Netlify. Se
alguma imagem ficar apontando para o nosso domínio, a página dele quebra no dia
em que o painel sair do ar. Estes testes travam isso.

    python manage.py test apps.pressel
"""
import base64
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.pressel import exportador
from apps.pressel.models import Pressel
from apps.pressel.templatetags.pressel_extras import hex_rgba, virgula_ponto


def _png(cor=(255, 0, 0), tamanho=(60, 60)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', tamanho, cor).save(buf, format='PNG')
    return SimpleUploadedFile('t.png', buf.getvalue(), content_type='image/png')


class FiltrosTest(TestCase):
    def test_brilho_sai_com_ponto_e_nao_virgula(self):
        # pt-BR + L10N renderizaria 0,45 — e brightness(0,45) e CSS invalido.
        self.assertEqual(virgula_ponto(0.45), '0.45')
        self.assertEqual(virgula_ponto(1.0), '1')

    def test_hex_para_rgba(self):
        self.assertEqual(hex_rgba('#0088cc', '0.4'), 'rgba(0, 136, 204, 0.4)')
        self.assertEqual(hex_rgba('#fff', '1'), 'rgba(255, 255, 255, 1.0)')

    def test_hex_invalido_nao_quebra(self):
        self.assertEqual(hex_rgba('xxx', '0.4'), 'rgba(0,0,0,0)')


class PresselModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d', password='x')
        self.p = Pressel.objects.create(owner=self.user, nome='Teste')

    def test_destaque_vira_span_rosa(self):
        self.p.descricao = 'liberei meu **canal privado** hoje'
        html = self.p.descricao_html()
        self.assertIn('<span class="highlight">canal privado</span>', html)

    def test_descricao_escapa_html(self):
        # O texto vai para um arquivo que o usuario publica: nao pode virar
        # porta de entrada para script.
        self.p.descricao = 'oi <script>alert(1)</script>'
        html = self.p.descricao_html()
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_card_vazio_nao_entra_na_lista(self):
        self.p.card1_texto = 'Tem texto'
        self.p.card2_texto = ''
        self.p.card3_texto = ''
        self.p.card4_texto = ''
        self.assertEqual(len(self.p.cards), 1)

    def test_fundo_cai_para_a_foto_de_perfil(self):
        self.p.foto_perfil = _png()
        self.p.save()
        self.assertEqual(self.p.imagens['fundo'], self.p.foto_perfil)


class ExportTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d2', password='x')
        self.p = Pressel.objects.create(
            owner=self.user, nome='Minha Pressel',
            nome_exibicao='Fulana', btn1_link='https://t.me/x',
            foto_perfil=_png(), card1_imagem=_png((0, 255, 0)),
            card1_texto='Conteúdo 🔒')

    def test_html_tem_a_estrutura_do_modelo(self):
        html = exportador.html_para_download(self.p)
        for marca in ('<div class="bg">', '<div class="overlay">',
                      'class="container"', 'class="model"',
                      'class="grid"', 'class="blur-img"', 'class="bottom-text"'):
            with self.subTest(marca):
                self.assertIn(marca, html)

    def test_textos_do_usuario_aparecem(self):
        html = exportador.html_para_download(self.p)
        self.assertIn('Fulana', html)
        self.assertIn('Conteúdo 🔒', html)
        self.assertIn('https://t.me/x', html)

    def test_imagens_vao_embutidas_e_nao_por_link(self):
        html = exportador.html_para_download(self.p)
        self.assertIn('data:image/', html)
        # nenhuma imagem pode apontar para /media/ (dominio do painel)
        self.assertNotIn('src="/media/', html)
        self.assertNotIn('url("/media/', html)

    def test_desfoque_configurado_entra_no_css(self):
        self.p.desfoque_fundo = 20
        self.p.desfoque_cards = 9
        self.p.save()
        html = exportador.html_para_download(self.p)
        self.assertIn('blur(20px)', html)
        self.assertIn('blur(9px)', html)

    def test_brilho_do_fundo_sai_valido(self):
        self.p.brilho_fundo = 0.45
        self.p.save()
        html = exportador.html_para_download(self.p)
        self.assertIn('brightness(0.45)', html)
        self.assertNotIn('brightness(0,45)', html)

    def test_previa_usa_url_e_nao_embute(self):
        html = exportador.html_para_previa(self.p)
        self.assertNotIn('data:image/', html)


class ViewsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d3', password='x')
        self.outro = User.objects.create_user(username='outro', password='x')
        self.client.force_login(self.user)
        self.p = Pressel.objects.create(owner=self.user, nome='Minha')

    def test_lista_abre(self):
        self.assertEqual(self.client.get(reverse('pressel:lista')).status_code, 200)

    def test_nova_cria_e_redireciona_para_o_editor(self):
        r = self.client.get(reverse('pressel:nova'))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Pressel.objects.filter(owner=self.user).count(), 2)

    def test_editor_abre(self):
        r = self.client.get(reverse('pressel:editar', args=[self.p.id]))
        self.assertEqual(r.status_code, 200)

    def test_download_vem_como_anexo_html(self):
        r = self.client.get(reverse('pressel:baixar', args=[self.p.id]))
        self.assertEqual(r.status_code, 200)
        self.assertIn('attachment', r['Content-Disposition'])
        self.assertIn('minha.html', r['Content-Disposition'])

    def test_nome_de_arquivo_sem_acento_nem_espaco(self):
        self.p.nome = 'Pressel da Béatriz & Cia'
        self.p.save()
        r = self.client.get(reverse('pressel:baixar', args=[self.p.id]))
        self.assertIn('pressel-da-beatriz-cia.html', r['Content-Disposition'])

    def test_duplicar_copia_os_campos(self):
        self.p.nome_exibicao = 'Fulana'
        self.p.save()
        self.client.post(reverse('pressel:duplicar', args=[self.p.id]))
        copia = Pressel.objects.filter(owner=self.user).exclude(id=self.p.id).first()
        self.assertEqual(copia.nome_exibicao, 'Fulana')
        self.assertIn('cópia', copia.nome)

    def test_nao_acesso_a_pressel_de_outro_dono(self):
        alheia = Pressel.objects.create(owner=self.outro, nome='Dele')
        for nome in ('editar', 'previa', 'baixar'):
            with self.subTest(nome):
                r = self.client.get(reverse(f'pressel:{nome}', args=[alheia.id]))
                self.assertEqual(r.status_code, 404)

    def test_exige_login(self):
        self.client.logout()
        r = self.client.get(reverse('pressel:lista'))
        self.assertEqual(r.status_code, 302)


class FormularioTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d4', password='x')
        self.client.force_login(self.user)
        self.p = Pressel.objects.create(owner=self.user, nome='P')

    def _post(self, **extra):
        dados = {
            'nome': 'P', 'titulo_pagina': 'T', 'nome_exibicao': 'N',
            'descricao': 'oi', 'texto_online': 'on',
            'btn1_titulo': 'a', 'btn1_subtitulo': 'b',
            'btn1_cor_a': '#0088cc', 'btn1_cor_b': '#00aaff',
            'btn2_titulo': 'c', 'btn2_subtitulo': 'd',
            'btn2_cor_a': '#ff6b35', 'btn2_cor_b': '#ff8c42',
            'titulo_cards': 'Conteúdos', 'rodape': '18+',
            'desfoque_fundo': 12, 'brilho_fundo': 0.45,
            'desfoque_cards': 6, 'desfoque_cards_hover': 3,
        }
        dados.update(extra)
        return self.client.post(reverse('pressel:editar', args=[self.p.id]), dados)

    def test_salva(self):
        self._post(nome_exibicao='Nova')
        self.p.refresh_from_db()
        self.assertEqual(self.p.nome_exibicao, 'Nova')

    def test_desfoque_absurdo_e_limitado(self):
        self._post(desfoque_fundo=999, desfoque_cards=999)
        self.p.refresh_from_db()
        self.assertEqual(self.p.desfoque_fundo, 40)
        self.assertEqual(self.p.desfoque_cards, 30)

    def test_hover_nunca_fica_mais_borrado_que_o_normal(self):
        # Senão o efeito inverte: passar o mouse ESCONDERIA mais a imagem.
        self._post(desfoque_cards=5, desfoque_cards_hover=20)
        self.p.refresh_from_db()
        self.assertLessEqual(self.p.desfoque_cards_hover, self.p.desfoque_cards)
