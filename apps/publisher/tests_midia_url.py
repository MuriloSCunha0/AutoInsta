"""Regressão: a Meta rejeitava toda mídia com acento no nome do arquivo.

A Meta BAIXA o vídeo da URL que enviamos. Nome com acento fazia o downloader
dela devolver `status_code: ERROR` mudo e nada publicava. Estes testes travam
as duas defesas no lugar — se alguém remover uma delas, o teste quebra.

    python manage.py test apps.publisher.tests_midia_url
"""
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import SimpleTestCase

from apps.core_utils import nome_seguro, url_midia, url_segura


class NomeSeguroTest(SimpleTestCase):
    def test_remove_acento_preservando_a_letra(self):
        self.assertEqual(nome_seguro('nossa_história.mp4'), 'nossa_historia.mp4')

    def test_troca_espaco_e_caracteres_que_quebram_url(self):
        self.assertEqual(nome_seguro('meu vídeo #1 (top?).mp4'), 'meu_video_1_top.mp4')

    def test_saida_e_sempre_ascii(self):
        for entrada in ['ação.mp4', 'ÜBER.MOV', '日本語.mp4', 'café — cópia.mp4']:
            saida = nome_seguro(entrada)
            saida.encode('ascii')  # levanta UnicodeEncodeError se escapar algo
            self.assertNotIn(' ', saida)

    def test_nunca_devolve_nome_vazio(self):
        self.assertTrue(nome_seguro('日本語').startswith('arquivo'))
        self.assertTrue(nome_seguro('').startswith('arquivo'))

    def test_preserva_a_extensao(self):
        self.assertTrue(nome_seguro('vídeo.mp4').endswith('.mp4'))


class UrlSeguraTest(SimpleTestCase):
    def test_encoda_o_acento(self):
        self.assertEqual(
            url_segura('https://x.com/media/história.mp4'),
            'https://x.com/media/hist%C3%B3ria.mp4',
        )

    def test_e_idempotente(self):
        """Encodar duas vezes não pode virar %25C3%25B3."""
        uma = url_segura('https://x.com/media/história.mp4')
        self.assertEqual(url_segura(uma), uma)

    def test_nao_mexe_no_dominio_nem_no_esquema(self):
        self.assertEqual(url_segura('https://x.com/a.mp4'), 'https://x.com/a.mp4')

    def test_url_midia_monta_e_encoda(self):
        self.assertEqual(
            url_midia('https://x.com/', '/media/', 'reels/ação.mp4'),
            'https://x.com/media/reels/a%C3%A7%C3%A3o.mp4',
        )


class StorageSaneiaTest(SimpleTestCase):
    """O storage é o ponto de estrangulamento: nada entra com nome ruim."""

    def test_save_direto_saneia_o_nome(self):
        nome = default_storage.save('reels/vídeo de teste.mp4', ContentFile(b'x'))
        try:
            self.assertNotIn(' ', nome)
            nome.encode('ascii')
            self.assertTrue(nome.startswith('reels/'))
        finally:
            default_storage.delete(nome)


class PublishMetaApiUrlTest(SimpleTestCase):
    """A URL tem de sair encodada de publish_meta_api, venha de onde vier."""

    def test_url_enviada_a_meta_vai_encodada(self):
        from engine.client import InstagramEngine

        conta = mock.Mock(meta_access_token='tok', ig_user_id='123', proxy_url='')
        conta.get_meta_token.return_value = 'tok'
        engine = InstagramEngine.__new__(InstagramEngine)
        engine.account = conta

        resp_ok = mock.Mock(status_code=200)
        resp_ok.json.return_value = {'id': 'container'}
        resp_status = mock.Mock(status_code=200)
        resp_status.json.return_value = {'status_code': 'FINISHED'}
        resp_pub = mock.Mock(status_code=200)
        resp_pub.json.return_value = {'id': 'media123'}

        with mock.patch('requests.get', side_effect=[resp_ok, resp_status]) as get, \
             mock.patch('requests.post', side_effect=[resp_ok, resp_pub]) as post, \
             mock.patch('time.sleep'):
            engine.publish_meta_api(
                media_url='https://x.com/media/história.mp4',
                caption='oi', post_type='REELS',
            )

        enviado = post.call_args_list[0].kwargs['data']['video_url']
        self.assertEqual(enviado, 'https://x.com/media/hist%C3%B3ria.mp4')
        self.assertNotIn('ó', enviado)
        # E a pré-checagem realmente bateu na URL antes de acionar a Meta.
        self.assertEqual(get.call_args_list[0].args[0], enviado)
