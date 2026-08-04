"""Gerador de CTA: a arte sai em 9:16 e vai parar na Biblioteca.

O adesivo é DESENHADO na imagem (não é figurinha nativa) justamente para
funcionar nas contas que publicam só pela API oficial — a Graph não aceita
anexar sticker. A arte entra como MediaAsset para o Composer poder postá-la
sem passo intermediário.

    python manage.py test apps.library.tests_cta
"""
import io
import os
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.library.models import MediaAsset, MediaFolder
from engine.cta_render import ALTURA, LARGURA, gerar_cta


def _jpg(cor=(90, 40, 60), tam=(720, 1280)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', tam, cor).save(buf, format='JPEG')
    return buf.getvalue()


def _arquivo_temp(dados=None):
    fd, caminho = tempfile.mkstemp(suffix='.jpg')
    with os.fdopen(fd, 'wb') as fh:
        fh.write(dados or _jpg())
    return caminho


class RenderTest(TestCase):
    def setUp(self):
        self.base = _arquivo_temp()
        self.addCleanup(lambda: os.path.exists(self.base) and os.remove(self.base))

    def _gerar(self, **kw):
        fd, destino = tempfile.mkstemp(suffix='.jpg')
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(destino) and os.remove(destino))
        return gerar_cta(self.base, destino=destino, **kw)

    def test_saida_e_sempre_9_por_16(self):
        from PIL import Image
        # A base é 720x1280 (9:16) mas também testamos uma quadrada, que precisa
        # ser cortada e não esticada.
        for tam in ((720, 1280), (900, 900), (1600, 900)):
            with self.subTest(tam=tam):
                base = _arquivo_temp(_jpg(tam=tam))
                fd, destino = tempfile.mkstemp(suffix='.jpg')
                os.close(fd)
                gerar_cta(base, tipo='link', sticker_texto='OI', destino=destino)
                with Image.open(destino) as im:
                    self.assertEqual(im.size, (LARGURA, ALTURA))
                os.remove(base)
                os.remove(destino)

    def test_gera_todos_os_tipos_de_adesivo(self):
        for tipo, _ in (('link', ''), ('enquete', ''), ('pergunta', ''),
                        ('contagem', ''), ('nenhum', '')):
            with self.subTest(tipo=tipo):
                saida = self._gerar(tipo=tipo, sticker_texto='Teste',
                                    opcao_a='SIM', opcao_b='NAO', titulo='Chamada')
                self.assertTrue(os.path.exists(saida))
                self.assertGreater(os.path.getsize(saida), 1000)

    def test_funciona_sem_imagem_de_fundo(self):
        # Sem foto, cai num fundo escuro em vez de estourar.
        fd, destino = tempfile.mkstemp(suffix='.jpg')
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(destino) and os.remove(destino))
        saida = gerar_cta(None, tipo='link', sticker_texto='CLIQUE', destino=destino)
        self.assertTrue(os.path.exists(saida))

    def test_texto_vazio_nao_quebra(self):
        saida = self._gerar(tipo='link', titulo='', sticker_texto='')
        self.assertTrue(os.path.exists(saida))

    def test_escala_do_adesivo_e_limitada(self):
        # Escala absurda não pode estourar o quadro nem derrubar o render.
        for escala in (-5, 0, 99):
            with self.subTest(escala=escala):
                saida = self._gerar(tipo='link', sticker_texto='X', sticker_escala=escala)
                self.assertTrue(os.path.exists(saida))

    def test_glifo_que_a_fonte_nao_tem_e_removido(self):
        # Emoji NÃO entra aqui (é desenhado à parte, ver EmojiTest). O que a
        # fonte de texto não desenha viraria quadradinho na arte do cliente.
        from engine.cta_render import _limpar_incompativel, _fonte
        # U+F8FF é da Área de Uso Privado: nenhuma fonte de texto tem.
        limpo = _limpar_incompativel('Olha isso ', _fonte(40))
        self.assertNotIn('', limpo)
        self.assertIn('Olha isso', limpo)

    def test_texto_longo_quebra_em_linhas(self):
        saida = self._gerar(titulo='palavra ' * 40, tipo='nenhum')
        self.assertTrue(os.path.exists(saida))


class ViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d', password='x')
        self.client.force_login(self.user)

    def test_tela_abre(self):
        r = self.client.get(reverse('library:cta'))
        self.assertEqual(r.status_code, 200)

    def test_exige_login(self):
        self.client.logout()
        r = self.client.get(reverse('library:cta'))
        self.assertEqual(r.status_code, 302)

    def _post(self, **extra):
        dados = {'tipo': 'link', 'sticker_texto': 'CLIQUE AQUI',
                 'titulo': 'Chamada', 'titulo_cor': '#ffffff',
                 'titulo_tamanho': 72, 'titulo_y': 0.16,
                 'sticker_y': 0.62, 'sticker_escala': 1,
                 'escurecer': 0.25, 'nome': 'minha-arte'}
        dados.update(extra)
        dados['imagem'] = SimpleUploadedFile('f.jpg', _jpg(), content_type='image/jpeg')
        return self.client.post(reverse('library:cta'), dados, follow=True)

    def test_gera_e_salva_na_biblioteca(self):
        r = self._post()
        self.assertEqual(r.status_code, 200)
        asset = MediaAsset.objects.filter(owner=self.user).first()
        self.assertIsNotNone(asset)
        self.assertEqual(asset.kind, 'image')
        self.assertIn('minha-arte', asset.original_name)

    def test_arte_salva_tem_o_tamanho_do_story(self):
        from PIL import Image
        self._post()
        asset = MediaAsset.objects.filter(owner=self.user).first()
        with Image.open(asset.file.path) as im:
            self.assertEqual(im.size, (LARGURA, ALTURA))

    def test_salva_na_pasta_escolhida(self):
        pasta = MediaFolder.objects.create(owner=self.user, name='CTAs')
        self._post(pasta=pasta.id)
        asset = MediaAsset.objects.filter(owner=self.user).first()
        self.assertEqual(asset.folder_id, pasta.id)

    def test_valores_fora_da_faixa_nao_quebram(self):
        r = self._post(titulo_tamanho=99999, sticker_escala=-3, escurecer=5)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(MediaAsset.objects.filter(owner=self.user).exists())

    def test_nao_usa_imagem_de_outro_dono(self):
        outro = User.objects.create_user(username='o', password='x')
        alheia = MediaAsset.objects.create(
            owner=outro, kind='image', original_name='dele.jpg')
        alheia.file.save('dele.jpg', SimpleUploadedFile('dele.jpg', _jpg()), save=True)
        # Sem upload próprio e apontando para a mídia alheia: gera sobre fundo
        # neutro, nunca sobre a foto do outro usuário.
        r = self.client.post(reverse('library:cta'), {
            'tipo': 'link', 'sticker_texto': 'X', 'nome': 'tenta',
            'imagem_biblioteca': alheia.id,
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        minha = MediaAsset.objects.filter(owner=self.user).first()
        self.assertIsNotNone(minha)


class EmojiTest(TestCase):
    """Emoji tem que APARECER na arte (colorido), nao ser removido."""

    def setUp(self):
        self.base = _arquivo_temp()
        self.addCleanup(lambda: os.path.exists(self.base) and os.remove(self.base))

    def test_fatiar_separa_texto_de_emoji(self):
        from engine.cta_render import _fatiar
        partes = _fatiar('Olha 🔥 isso')
        self.assertIn((True, '🔥'), partes)
        self.assertIn((False, 'Olha '), partes)

    def test_emoji_nao_e_mais_removido_do_texto(self):
        # Antes o filtro tirava o emoji; agora ele e desenhado a parte.
        from engine.cta_render import _limpar_incompativel, _fonte
        limpo = _limpar_incompativel('Liberei 🔥', _fonte(40))
        self.assertIn('🔥', limpo)

    def test_emoji_ocupa_largura_na_medicao(self):
        # Se o emoji nao entrasse na conta, o texto sairia descentralizado.
        from PIL import Image, ImageDraw
        from engine.cta_render import _largura, _fonte, tem_suporte_a_emoji
        if not tem_suporte_a_emoji():
            self.skipTest('sem fonte de emoji nesta maquina')
        d = ImageDraw.Draw(Image.new('RGB', (10, 10)))
        f = _fonte(40)
        self.assertGreater(_largura(d, 'ab🔥', f), _largura(d, 'ab', f))

    def test_arte_com_emoji_gera(self):
        fd, destino = tempfile.mkstemp(suffix='.jpg')
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(destino) and os.remove(destino))
        gerar_cta(self.base, tipo='link', titulo='Liberei tudo 🔥🔥',
                  sticker_texto='CLIQUE AQUI 👉', destino=destino)
        self.assertGreater(os.path.getsize(destino), 1000)


class PreviaTest(TestCase):
    """A previa devolve o JPG e NAO salva nada na biblioteca."""

    def setUp(self):
        self.user = User.objects.create_user(username='p', password='x')
        self.client.force_login(self.user)

    def test_previa_devolve_imagem(self):
        r = self.client.post(reverse('library:cta_previa'), {
            'tipo': 'link', 'sticker_texto': 'OI', 'titulo': 'T',
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'image/jpeg')
        self.assertGreater(len(r.content), 1000)

    def test_previa_nao_salva_na_biblioteca(self):
        self.client.post(reverse('library:cta_previa'), {'tipo': 'link', 'sticker_texto': 'X'})
        self.assertFalse(MediaAsset.objects.filter(owner=self.user).exists())

    def test_previa_tem_o_tamanho_do_story(self):
        from PIL import Image
        r = self.client.post(reverse('library:cta_previa'), {'tipo': 'link'})
        with Image.open(io.BytesIO(r.content)) as im:
            self.assertEqual(im.size, (LARGURA, ALTURA))

    def test_previa_reaproveita_a_foto_da_sessao(self):
        # 1a chamada manda a foto; a 2a manda so os textos e ainda usa a foto.
        env = SimpleUploadedFile('f.jpg', _jpg(), content_type='image/jpeg')
        r1 = self.client.post(reverse('library:cta_previa'),
                              {'tipo': 'link', 'sticker_texto': 'A', 'imagem': env})
        self.assertEqual(r1.status_code, 200)
        self.assertIn('cta_base', self.client.session)
        r2 = self.client.post(reverse('library:cta_previa'),
                              {'tipo': 'link', 'sticker_texto': 'B'})
        self.assertEqual(r2.status_code, 200)
        self.assertNotEqual(r1.content, r2.content)   # o texto mudou

    def test_previa_exige_login(self):
        self.client.logout()
        r = self.client.post(reverse('library:cta_previa'), {'tipo': 'link'})
        self.assertEqual(r.status_code, 302)
