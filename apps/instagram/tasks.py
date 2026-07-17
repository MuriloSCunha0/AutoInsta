from celery import shared_task
from engine.client import InstagramEngine
from .models import InstagramAccount

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
