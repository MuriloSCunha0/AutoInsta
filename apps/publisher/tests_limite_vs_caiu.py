"""Regressão: conta LIMITADA pela Meta não pode ser marcada como CAÍDA.

Bug relatado pelo usuário iorio (04/08/2026): 3 contas apareciam no painel como
"Conta caiu — entre no instagram.com e veja se está SUSPENSA", mas o token delas
respondia HTTP 200 na Graph API e a cota estava em 50/100. Elas estavam apenas
no limite de publicação.

Causa: a Meta devolve os erros de LIMITE com o MESMO `type: OAuthException` de
um token inválido — só o `code` distingue:

    code 4   — Application request limit reached
    code 9   — usuário atingiu o número máximo de publicações
    code 17  — User request limit reached
    code 32  — Page request limit reached
    code 613 — Calls to this api have exceeded the rate limit
    code 190 — token realmente inválido

`_e_app_invalido` casava com 'oauthexception' solto e era avaliada antes de
`_e_rate_limit`, então TODO erro de limite virava "token morto": a conta ia para
`status='error'`, a guarda anti-martelo travava a fila dela e o dono via a
mensagem de conta suspensa.

    python manage.py test apps.publisher.tests_limite_vs_caiu
"""
from django.test import SimpleTestCase

from apps.core_utils import msg_meta_amigavel
from apps.publisher.tasks import _e_app_invalido, _e_rate_limit


# Respostas reais da Meta, no formato em que chegam em str(exception).
LIMITES = {
    'code 4 — application request limit': (
        "Erro ao criar contêiner Meta: {'message': '(#4) Application request "
        "limit reached', 'type': 'OAuthException', 'code': 4, "
        "'fbtrace_id': 'ABC123'}"
    ),
    'code 9 — máximo de publicações': (
        "Erro ao criar contêiner Meta: {'message': '(#9) The user has reached "
        "the maximum number of posts', 'type': 'OAuthException', 'code': 9}"
    ),
    'code 17 — user request limit': (
        "Erro ao criar contêiner Meta: {'message': '(#17) User request limit "
        "reached', 'type': 'OAuthException', 'code': 17}"
    ),
    'code 32 — page request limit': (
        "Erro ao criar contêiner Meta: {'message': '(#32) Page request limit "
        "reached', 'type': 'OAuthException', 'code': 32}"
    ),
    'code 613 — rate limit': (
        "Erro ao criar contêiner Meta: {'message': '(#613) Calls to this api "
        "have exceeded the rate limit', 'type': 'OAuthException', 'code': 613}"
    ),
    'cota 2207042': (
        "Erro ao criar contêiner Meta: {'message': 'The number of media posts "
        "exceeded the limit', 'error_subcode': 2207042, "
        "'type': 'OAuthException', 'code': 9}"
    ),
}

# O erro REAL de conta em checkpoint, capturado na produção em 04/08/2026.
TOKEN_MORTO = {
    'checkpoint na conta (190)': (
        "Erro ao criar contêiner Meta: {'message': 'Error validating access "
        "token: You cannot access the app till you log in to "
        "www.instagram.com and follow the instructions given.', "
        "'type': 'OAuthException', 'code': 190, 'error_subcode': 0}"
    ),
    'token invalido (190)': (
        "{'message': 'Error validating access token: Session key is malformed "
        "because of invalid user id.', 'type': 'OAuthException', 'code': 190}"
    ),
    'sessao invalidada': (
        "{'message': 'The session has been invalidated because the user "
        "changed their password', 'type': 'OAuthException', 'code': 190}"
    ),
}


class LimiteNaoEhQuedaTest(SimpleTestCase):
    def test_erros_de_limite_sao_reconhecidos_como_limite(self):
        for rotulo, msg in LIMITES.items():
            with self.subTest(rotulo):
                self.assertTrue(_e_rate_limit(msg), f'{rotulo} deveria ser limite')

    def test_erros_de_limite_NAO_marcam_a_conta_como_caida(self):
        """O coração do bug do iorio."""
        for rotulo, msg in LIMITES.items():
            with self.subTest(rotulo):
                self.assertFalse(
                    _e_app_invalido(msg),
                    f'{rotulo} nao pode ser tratado como token/app invalido')

    def test_mensagem_de_limite_nao_fala_em_conta_suspensa(self):
        for rotulo, msg in LIMITES.items():
            with self.subTest(rotulo):
                amigavel = msg_meta_amigavel(msg).lower()
                self.assertIn('limite', amigavel)
                self.assertNotIn('suspensa', amigavel)


class TokenMortoContinuaSendoQuedaTest(SimpleTestCase):
    """A correção não pode ter afrouxado o outro lado: 190 continua queda."""

    def test_190_e_app_invalido(self):
        for rotulo, msg in TOKEN_MORTO.items():
            with self.subTest(rotulo):
                self.assertTrue(_e_app_invalido(msg), f'{rotulo} deveria ser queda')

    def test_190_nao_e_confundido_com_limite(self):
        for rotulo, msg in TOKEN_MORTO.items():
            with self.subTest(rotulo):
                self.assertFalse(_e_rate_limit(msg), f'{rotulo} nao e limite')

    def test_mensagem_de_190_orienta_a_reconectar(self):
        amigavel = msg_meta_amigavel(TOKEN_MORTO['checkpoint na conta (190)']).lower()
        self.assertIn('instagram.com', amigavel)


class OAuthExceptionGenericaTest(SimpleTestCase):
    def test_oauthexception_sozinha_nao_derruba_mais_a_conta(self):
        # Era o catch-all que causava o bug: qualquer OAuthException virava
        # "app inválido". Sem um code de token, não derruba mais.
        msg = "{'message': 'Alguma coisa', 'type': 'OAuthException', 'code': 1}"
        self.assertFalse(_e_app_invalido(msg))

    def test_190_e_reconhecido_em_qualquer_serializacao(self):
        # O erro chega como texto e o formato varia conforme o caminho.
        for msg in ("OAuthException code 190",
                    "{'code': 190}", '{"code": 190}',
                    "(#190) alguma coisa", "code: 190"):
            with self.subTest(msg):
                self.assertTrue(_e_app_invalido(msg))
