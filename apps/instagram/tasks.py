import time

from celery import shared_task
from django.core.cache import cache

from engine.client import InstagramEngine
from engine.web_login import perform_web_login, CODE_WAIT_S
from .models import InstagramAccount


def _code_cache_key(account_id):
    return f"ig_login_code:{account_id}"


# time_limit MAIOR que CODE_WAIT_S (300s): a task fica viva com o navegador
# aberto enquanto espera o usuário digitar o código de 2FA/checkpoint.
@shared_task(soft_time_limit=360, time_limit=380)
def web_login_account(account_id):
    """Login REAL no instagram.com via navegador (Playwright).

    O usuário só informa usuário e senha. Se o IG pedir código (2FA por app ou
    checkpoint por e-mail/SMS), marcamos a conta ('2fa_required'/'challenge_required'),
    a UI mostra o campo, e o navegador fica ABERTO aguardando — o código chega
    por cache (submit_challenge grava a chave) e o digitamos na tela viva.
    No sucesso, importamos o sessionid capturado para o instagrapi.
    """
    try:
        account = InstagramAccount.objects.get(id=account_id)
    except InstagramAccount.DoesNotExist:
        return

    def on_code_needed(kind):
        account.status = '2fa_required' if kind == 'twofa' else 'challenge_required'
        account.save(update_fields=['status'])

    def code_getter():
        # Bloqueia aguardando o usuário digitar o código na plataforma.
        key = _code_cache_key(account_id)
        cache.delete(key)
        deadline = time.time() + CODE_WAIT_S
        while time.time() < deadline:
            code = cache.get(key)
            if code:
                cache.delete(key)
                return code
            time.sleep(2)
        return None

    account.status = 'connecting'
    account.save(update_fields=['status'])

    result = perform_web_login(
        account.ig_username,
        account.get_ig_password(),
        proxy_url=account.proxy_url or None,
        on_code_needed=on_code_needed,
        code_getter=code_getter,
    )

    status = result.get('status')
    if status == 'success':
        # Reaproveita o motor: constrói a sessão instagrapi a partir do
        # sessionid e busca foto/seguidores/etc.
        try:
            engine = InstagramEngine(account)
            engine.login_by_session(result['sessionid'])
        except Exception as e:
            account.refresh_from_db()
            if account.status != 'active':
                account.status = 'error'
                account.last_error = f'Sessão capturada, mas falhou ao sincronizar: {e}'
                account.save()
    elif status == 'bad_password':
        account.status = 'error'
        account.last_error = 'Usuário ou senha incorretos (confirmado no login web do Instagram).'
        account.save()
    elif status == 'twofa_required':
        account.status = '2fa_required'
        account.last_error = result.get('message', '')
        account.save()
    elif status == 'checkpoint':
        account.status = 'challenge_required'
        account.last_error = result.get('message', '')
        account.save()
    else:
        account.status = 'error'
        account.last_error = result.get('message', 'Falha no login web.')
        account.save()


@shared_task
def login_instagram_account(account_id):
    try:
        account = InstagramAccount.objects.get(id=account_id)
        engine = InstagramEngine(account)
        engine.login()
    except Exception as e:
        print(f"Error logging in account {account_id}: {str(e)}")

@shared_task
def connect_by_sessionid(account_id, sessionid):
    try:
        account = InstagramAccount.objects.get(id=account_id)
        engine = InstagramEngine(account)
        engine.login_by_session(sessionid)
    except Exception as e:
        print(f"Error connecting account {account_id} by session: {str(e)}")

@shared_task
def submit_challenge_code(account_id, code):
    try:
        account = InstagramAccount.objects.get(id=account_id)
        engine = InstagramEngine(account)
        engine.resolve_challenge(code)
    except Exception as e:
        print(f"Error submitting challenge for {account_id}: {str(e)}")

@shared_task
def submit_2fa_code(account_id, code):
    try:
        account = InstagramAccount.objects.get(id=account_id)
        engine = InstagramEngine(account)
        engine.resolve_2fa(code)
    except Exception as e:
        print(f"Error submitting 2FA for {account_id}: {str(e)}")
