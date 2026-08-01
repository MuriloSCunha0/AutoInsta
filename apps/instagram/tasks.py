import logging
import time
from celery import shared_task
from django.core.cache import cache

from engine.client import InstagramEngine
from .models import InstagramAccount

logger = logging.getLogger('connect')

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

    logger.info("[CONNECT acc=%s @%s] web_login_account: iniciando (gen=%s)",
                account.id, account.ig_username, login_gen)

    engine = InstagramEngine(account, code_getter=lambda: _poll_redis_for_code(account_id, login_gen))

    try:
        engine.login()
        logger.info("[CONNECT acc=%s @%s] web_login_account: concluiu status=%s",
                    account.id, account.ig_username, account.status)
    except Exception as e:
        # engine.login() já gravou status/last_error; aqui garantimos o rastro
        # no log (o traceback já saiu dentro do engine para erros inesperados).
        logger.warning("[CONNECT acc=%s @%s] web_login_account: terminou em erro status=%s last_error=%s",
                       account.id, account.ig_username, account.status,
                       (account.last_error or '')[:300])

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

    from django.utils import timezone as _tz

    agora = int(time.time())
    # "Hoje" ancorado à meia-noite de Brasília (TIME_ZONE): sem since/until a
    # Meta devolve uma janela ambígua (~25-48h) que inflava o número. Assim é
    # exatamente o dia-calendário do usuário.
    inicio_dia = _tz.localtime(_tz.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    hoje = pedir({'since': int(inicio_dia.timestamp()), 'until': agora})
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

        # Seguidores: sem isto o número só era atualizado ao conectar a conta e
        # ficava velho (visto em produção: 139 gravado × 151 na API).
        try:
            perfil = requests.get(
                f"https://graph.instagram.com/{IG_API_VERSION}/me",
                params={'fields': 'name,profile_picture_url,followers_count,'
                                  'follows_count,media_count',
                        'access_token': token}, timeout=15,
            ).json()
            if 'error' not in perfil:
                campos = []
                for chave, atributo in (('followers_count', 'followers_count'),
                                        ('follows_count', 'following_count'),
                                        ('media_count', 'posts_count')):
                    if perfil.get(chave) is not None:
                        setattr(acc, atributo, perfil[chave])
                        campos.append(atributo)
                # name/foto também: sem isto o card ficava eternamente em
                # "Aguardando sincronização..." mesmo com a conta publicando,
                # porque só a conexão inicial buscava esses campos.
                if perfil.get('name'):
                    acc.full_name = perfil['name'][:255]
                    campos.append('full_name')
                if perfil.get('profile_picture_url'):
                    acc.profile_pic_url = perfil['profile_picture_url'][:1000]
                    campos.append('profile_pic_url')
                if campos:
                    acc.save(update_fields=campos)
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
    except InstagramAccount.DoesNotExist:
        logger.warning("[CONNECT acc=%s] connect_by_sessionid: conta nao existe", account_id)
        return
    logger.info("[CONNECT acc=%s @%s] connect_by_sessionid: iniciando", account.id, account.ig_username)
    try:
        engine = InstagramEngine(account)
        engine.login_by_session(sessionid)
        logger.info("[CONNECT acc=%s @%s] connect_by_sessionid: concluiu status=%s",
                    account.id, account.ig_username, account.status)
    except Exception as e:
        logger.warning("[CONNECT acc=%s @%s] connect_by_sessionid: erro status=%s last_error=%s",
                       account.id, account.ig_username, account.status, (account.last_error or '')[:300])


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
def bulk_edit_profiles(account_ids, full_name, biography, external_url, picture_name=None, to_creator=False):
    """Edita bio/nome/link (e opcionalmente foto) de várias contas de uma vez, e
    opcionalmente converte para conta de CRIADOR DE CONTEÚDO. Aplica spintax
    ({nome_conta}) por conta. Só funciona em contas com sessão (engine)."""
    import os
    from apps.core_utils import midia_local_por_nome

    # A foto é salva no painel; o braço baixa uma cópia local UMA vez.
    pic_local, pic_tmp = (None, False)
    if picture_name:
        try:
            pic_local, pic_tmp = midia_local_por_nome(picture_name)
        except Exception as e:
            logger.warning("bulk_edit: falha ao obter a foto (%s): %s", picture_name, str(e)[:120])
            pic_local = None

    try:
        for acc_id in account_ids:
            try:
                account = InstagramAccount.objects.get(id=acc_id)
            except InstagramAccount.DoesNotExist:
                continue

            # Editar perfil/converter tipo não existe na API oficial: exige a engine.
            if not account.tem_sessao_engine:
                account.last_error = ('Edição de perfil requer conexão por sessão/senha — '
                                      'a API oficial da Meta não permite alterar bio/nome/foto.')
                account.save(update_fields=['last_error'])
                continue

            try:
                engine = InstagramEngine(account)

                if to_creator:
                    try:
                        engine.convert_to_creator()
                    except Exception as e:
                        logger.warning("bulk_edit: converter @%s p/ criador falhou: %s",
                                       account.ig_username, str(e)[:120])

                bio = (biography or '').replace('{nome_conta}', account.ig_username) if biography else None
                name = (full_name or '').replace('{nome_conta}', account.ig_username) if full_name else None
                link = external_url or None

                if bio is not None or name is not None or link is not None:
                    engine.edit_profile(full_name=name, biography=bio, external_url=link)
                if pic_local:
                    engine.change_profile_picture(pic_local)

                account.last_error = ''
                account.save(update_fields=['last_error'])
            except Exception as e:
                account.last_error = f"Falha ao editar perfil: {str(e)[:200]}"
                account.save(update_fields=['last_error'])
    finally:
        if pic_tmp and pic_local and os.path.exists(pic_local):
            try:
                os.remove(pic_local)
            except Exception:
                pass


# =============================================================================
# Keep-alive das sessões (mantém o cookie vivo -> conta de sessionid cai menos)
# =============================================================================
@shared_task
def keepalive_sessions():
    """Dispatcher (Celery Beat): despacha um keepalive por conta ATIVA que tem
    sessão salva. Validar/renovar a sessão de leve periodicamente adia MUITO a
    expiração do cookie — é o que reduz as quedas de contas conectadas por
    sessionid. O trabalho de rede vai para a fila `publisher` (IP limpo)."""
    ids = list(
        InstagramAccount.objects.filter(status='active', session_blob__isnull=False)
        .values_list('id', flat=True)
    )
    for aid in ids:
        keepalive_account.delay(aid)
    return f"keepalive despachado para {len(ids)} conta(s)"


@shared_task(soft_time_limit=120, time_limit=140)
def keepalive_account(account_id):
    """Valida a sessão salva de UMA conta e a re-salva (renova/estende). Se a
    sessão morreu e a conta é só-sessão, marca `session_expired` para aparecer
    em 'contas que precisam de atenção'."""
    from engine.session_manager import SessionManager
    from instagrapi.exceptions import LoginRequired
    acc = InstagramAccount.objects.filter(id=account_id).first()
    if not acc or not acc.session_blob:
        return
    eng = InstagramEngine(acc)
    try:
        eng._aplicar_proxy()
        SessionManager.load_session(acc, eng.client)
        eng.client.account_info()               # validação leve e CONFIÁVEL
        SessionManager.save_session(acc, eng.client)  # re-salva (renova)
        # Sessão viva: limpa a flag e reativa se estava caída só por causa dela.
        campos = []
        if acc.sessao_expirada:
            acc.sessao_expirada = False
            campos.append('sessao_expirada')
        if acc.status in ('session_expired',) or (acc.status != 'active' and campos):
            acc.status = 'active'
            acc.last_error = ''
            campos += ['status', 'last_error']
        if campos:
            acc.save(update_fields=list(set(campos)))
    except LoginRequired:
        # Sessão REALMENTE morta. HÍBRIDO: se a conta tem token OAuth, NÃO derruba
        # — só marca a sessão caída (story-link/aquecimento esperam recolar). Sem
        # token (só-sessão), aí sim vira session_expired (não há outra via).
        acc.sessao_expirada = True
        if acc.meta_access_token:
            campos = ['sessao_expirada', 'last_error']
            acc.last_error = ('Sessão do story-link caiu (keep-alive) — recole o '
                              'sessionid no card. O resto segue pelo OAuth.')
            if acc.status == 'session_expired':
                acc.status = 'active'
                campos.append('status')
            acc.save(update_fields=campos)
        else:
            try:
                senha = acc.get_ig_password()
            except Exception:
                senha = ''
            if senha in ('', '__session_login__'):
                acc.status = 'session_expired'
                acc.last_error = ('Sessão expirada (keep-alive). Reconecte pela aba '
                                  '"Sessão" — cole o cookie do Instagram de novo.')
                acc.save(update_fields=['status', 'last_error', 'sessao_expirada'])
    except Exception as e:
        # Erro transitório (rate limit/rede) — NÃO marca expirada.
        logger.info("[KEEPALIVE] @%s check transitório: %s", acc.ig_username, str(e)[:100])
