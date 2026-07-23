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

# A Meta guarda no máximo 2 anos de insights; pedir mais devolve
# "since param is not valid". 729 dias é o maior intervalo aceito na prática.
JANELA_TOTAL_DIAS = 729


def buscar_views(account):
    """Views reais da Meta: (do dia, total). Devolve (None, None) se não der.

    Endpoint e parâmetros conferidos contra a API de produção:
      GET /{ig-user-id}/insights?metric=views&period=day&metric_type=total_value
    `metric_type=total_value` é OBRIGATÓRIO — sem ele a Meta devolve `data: []`.
    Sem since/until o retorno é o dia corrente; com since/until, `total_value`
    já vem agregado no intervalo (não é uma série que precise ser somada).
    """
    import time

    import requests
    from apps.instagram.views import IG_API_VERSION

    token = account.get_meta_token()
    if not token or not account.ig_user_id:
        return None, None

    url = f"https://graph.instagram.com/{IG_API_VERSION}/{account.ig_user_id}/insights"

    def pedir(extra=None):
        params = {'metric': 'views', 'period': 'day',
                  'metric_type': 'total_value', 'access_token': token}
        params.update(extra or {})
        dados = requests.get(url, params=params, timeout=20).json()
        if 'error' in dados:
            return None
        for item in dados.get('data', []):
            if item.get('name') == 'views':
                return (item.get('total_value') or {}).get('value')
        return None

    agora = int(time.time())
    hoje = pedir()
    total = pedir({'since': agora - JANELA_TOTAL_DIAS * 86400, 'until': agora})
    return hoje, total


@shared_task
def refresh_quotas():
    """Atualiza cota de publicação e visualizações de todas as contas com token.
    Leve e best-effort: uma conta que falhar não derruba as outras."""
    import requests
    from django.utils import timezone
    from apps.instagram.views import IG_API_VERSION

    contas = InstagramAccount.objects.exclude(meta_access_token='').exclude(ig_user_id__isnull=True)
    for acc in contas:
        token = acc.get_meta_token()
        if not token:
            continue
        try:
            data = requests.get(
                f"https://graph.instagram.com/{IG_API_VERSION}/{acc.ig_user_id}/content_publishing_limit",
                params={'fields': 'config,quota_usage', 'access_token': token}, timeout=15,
            ).json()
            dados = (data.get('data') or [{}])[0]
            if 'quota_usage' in dados:
                acc.quota_usage = dados.get('quota_usage', 0)
                acc.quota_total = (dados.get('config') or {}).get('quota_total', 0)
                acc.quota_checked_at = timezone.now()
                acc.save(update_fields=['quota_usage', 'quota_total', 'quota_checked_at'])
        except Exception:
            pass

        try:
            hoje, total = buscar_views(acc)
            campos = []
            if hoje is not None:
                acc.views_today = hoje
                campos.append('views_today')
            if total is not None:
                acc.views_total = total
                campos.append('views_total')
            if campos:
                acc.views_checked_at = timezone.now()
                acc.save(update_fields=campos + ['views_checked_at'])
        except Exception:
            pass


@shared_task
def connect_by_sessionid(account_id, sessionid):
    try:
        account = InstagramAccount.objects.get(id=account_id)
        engine = InstagramEngine(account)
        engine.login_by_session(sessionid)
    except Exception as e:
        pass


# =============================================================================
# Onda 4 — Diferenciais da engine (warm-up e edição de perfil em massa)
# =============================================================================
# Lote pequeno por execução (o beat roda a cada 30min → várias execuções/dia
# distribuem o alvo diário sem picos robóticos).
WARMUP_BATCH = {'likes': 4, 'follows': 1, 'views': 6}


@shared_task
def run_warmups():
    """Percorre as contas com warm-up ligado e executa um lote pequeno de ações,
    respeitando o alvo diário por intensidade."""
    from django.utils import timezone
    from .models import WarmupConfig

    today = timezone.localdate()
    for cfg in WarmupConfig.objects.filter(enabled=True).select_related('account'):
        # Reseta contadores no virar do dia.
        if cfg.counter_date != today:
            cfg.counter_date = today
            cfg.likes_today = cfg.follows_today = cfg.views_today = 0

        target_likes, target_follows, target_views = cfg.daily_targets
        batch_likes = max(min(WARMUP_BATCH['likes'], target_likes - cfg.likes_today), 0)
        batch_follows = max(min(WARMUP_BATCH['follows'], target_follows - cfg.follows_today), 0)
        batch_views = max(min(WARMUP_BATCH['views'], target_views - cfg.views_today), 0)

        if not (batch_likes or batch_follows or batch_views):
            cfg.save()
            continue

        run_account_warmup.delay(cfg.id, batch_likes, batch_follows, batch_views)
        cfg.save()


@shared_task(soft_time_limit=240, time_limit=280)
def run_account_warmup(config_id, likes, follows, views):
    from django.utils import timezone
    from .models import WarmupConfig

    try:
        cfg = WarmupConfig.objects.select_related('account').get(id=config_id)
    except WarmupConfig.DoesNotExist:
        return

    # A API oficial da Meta não permite curtir/seguir/ver: o aquecimento só
    # funciona pela engine, que exige sessão/senha. Sem isso, registramos o
    # motivo em vez de falhar em silêncio.
    if not cfg.account.tem_sessao_engine:
        cfg.last_result = ('Requer conexão por sessão/senha — a API oficial '
                           'não permite curtidas/follows.')
        cfg.last_run = timezone.now()
        cfg.save(update_fields=['last_result', 'last_run'])
        return

    try:
        engine = InstagramEngine(cfg.account)
        done = engine.run_warmup(likes=likes, follows=follows, views=views, hashtag=cfg.target_hashtag or 'reels')
        cfg.likes_today += done.get('likes', 0)
        cfg.follows_today += done.get('follows', 0)
        cfg.views_today += done.get('views', 0)
        cfg.last_result = f"+{done.get('likes',0)} curtidas, +{done.get('follows',0)} follows, +{done.get('views',0)} views"
    except Exception as e:
        cfg.last_result = f"Erro: {str(e)[:180]}"

    cfg.last_run = timezone.now()
    cfg.save()


@shared_task(soft_time_limit=300, time_limit=340)
def bulk_edit_profiles(account_ids, full_name, biography, external_url, picture_path=None):
    """Edita bio/nome/link (e opcionalmente foto) de várias contas de uma vez.
    Aplica spintax ({nome_conta}) por conta. Funciona em contas Pessoais."""
    for acc_id in account_ids:
        try:
            account = InstagramAccount.objects.get(id=acc_id)
        except InstagramAccount.DoesNotExist:
            continue

        # Editar bio/nome/foto não existe na API oficial: exige a engine.
        if not account.tem_sessao_engine:
            account.last_error = ('Edição de perfil requer conexão por sessão/senha — '
                                  'a API oficial da Meta não permite alterar bio/nome/foto.')
            account.save(update_fields=['last_error'])
            continue

        try:
            engine = InstagramEngine(account)

            bio = (biography or '').replace('{nome_conta}', account.ig_username) if biography else None
            name = (full_name or '').replace('{nome_conta}', account.ig_username) if full_name else None
            link = external_url or None

            if bio is not None or name is not None or link is not None:
                engine.edit_profile(full_name=name, biography=bio, external_url=link)
            if picture_path:
                engine.change_profile_picture(picture_path)

            account.last_error = ''
            account.save(update_fields=['last_error'])
        except Exception as e:
            account.last_error = f"Falha ao editar perfil: {str(e)[:200]}"
            account.save(update_fields=['last_error'])
