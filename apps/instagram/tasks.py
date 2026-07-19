import time
from celery import shared_task
from django.core.cache import cache

from engine.client import InstagramEngine
from .models import InstagramAccount

CODE_WAIT_S = 300

def _code_cache_key(account_id):
    return f"ig_login_code:{account_id}"

def _gen_cache_key(account_id):
    return f"ig_login_gen:{account_id}"

def claim_login_generation(account_id):
    key = _gen_cache_key(account_id)
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=None)
        return 1

def _poll_redis_for_code(account_id, login_gen):
    def _is_current():
        return cache.get(_gen_cache_key(account_id)) == login_gen

    key = _code_cache_key(account_id)
    cache.delete(key)
    deadline = time.time() + CODE_WAIT_S
    while time.time() < deadline:
        if not _is_current():
            return None
        code = cache.get(key)
        if code:
            cache.delete(key)
            return code
        time.sleep(2)
    return None

@shared_task(soft_time_limit=360, time_limit=380)
def web_login_account(account_id, login_gen=None):
    # Maintained same function name (web_login_account) so we don't break existing views.py calls,
    # but internally we use instagrapi for the 2FA bypass strategy.
    try:
        account = InstagramAccount.objects.get(id=account_id)
    except InstagramAccount.DoesNotExist:
        return

    if login_gen is None:
        login_gen = claim_login_generation(account_id)

    def _is_current():
        return cache.get(_gen_cache_key(account_id)) == login_gen

    if not _is_current():
        return

    account.status = 'connecting'
    account.save(update_fields=['status'])

    engine = InstagramEngine(account, code_getter=lambda: _poll_redis_for_code(account_id, login_gen))
    
    try:
        engine.login()
    except Exception as e:
        pass

@shared_task
def connect_by_sessionid(account_id, sessionid):
    try:
        account = InstagramAccount.objects.get(id=account_id)
        engine = InstagramEngine(account)
        engine.login_by_session(sessionid)
    except Exception as e:
        pass
