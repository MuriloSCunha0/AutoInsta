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

    # SÓ contas ATIVAS e não pausadas: bater na Graph (4 requests/conta) numa
    # conta já em 'error'/190/pausada é tráfego falho repetido contra um app já
    # restringido — agrava a punição. Contas caídas voltam ao religar o token.
    contas = (InstagramAccount.objects.filter(status='active', pausada=False)
              .exclude(meta_access_token='').exclude(ig_user_id__isnull=True))
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
# Aquecimento HUMANO: micro-ações ESPAÇADAS + ramp-up + consumo passivo primeiro.
# O espaçamento vem do AGENDAMENTO (o dispatcher decide 0-1 ação por conta por
# rodada, respeitando gap aleatório + horas ativas + alvo do dia por FASE), não
# de sleep numa tarefa longa. Cada ação é uma micro-tarefa curta que recarrega e
# RE-SALVA a sessão (mantém viva) — sem risco de expirar no meio.
# =============================================================================
@shared_task
def run_warmups():
    """Dispatcher (beat, roda a cada poucos min): agenda NO MÁXIMO 1 ação por
    conta saudável quando dá o gap humano, dentro das horas ativas."""
    import random
    from django.utils import timezone
    from django.conf import settings as _s
    from .models import WarmupConfig

    agora = timezone.now()
    hora = timezone.localtime(agora).hour
    h_ini = getattr(_s, 'WARMUP_HORA_INI', 8)
    h_fim = getattr(_s, 'WARMUP_HORA_FIM', 23)
    gap_min = getattr(_s, 'WARMUP_GAP_MIN', 12)   # minutos entre ações
    gap_max = getattr(_s, 'WARMUP_GAP_MAX', 30)
    today = timezone.localdate()

    for cfg in WarmupConfig.objects.filter(enabled=True).select_related('account'):
        a = cfg.account
        # Só conta 100% saudável e com sessão (curtir/seguir/ver é API privada).
        if (a.status != 'active' or a.sessao_expirada or a.pausada
                or a.banned_by_admin or not a.tem_sessao_engine):
            continue
        # Humano não fica ativo às 4h da manhã.
        if not (h_ini <= hora < h_fim):
            continue

        campos = []
        if cfg.counter_date != today:
            cfg.counter_date = today
            cfg.likes_today = cfg.follows_today = cfg.views_today = cfg.browse_today = 0
            campos += ['counter_date', 'likes_today', 'follows_today', 'views_today', 'browse_today']
        if not cfg.started_at:
            cfg.started_at = agora
            campos.append('started_at')
        if campos:
            cfg.save(update_fields=campos)

        # Gap humano (aleatório a cada checagem → jitter natural).
        if cfg.last_action_at:
            faltam_min = (agora - cfg.last_action_at).total_seconds() / 60.0
            if faltam_min < random.randint(gap_min, gap_max):
                continue

        # Pool de ações permitidas pela FASE (ramp-up) + alvo restante do dia.
        # Peso maior no consumo passivo (baixo risco); like/follow são o tempero.
        alvo_likes, alvo_follows, alvo_views = cfg.alvos_hoje()
        pool = []
        if cfg.browse_today < max(6, alvo_views):
            pool += ['browse'] * 4
        if cfg.views_today < alvo_views:
            pool += ['view'] * 4
        if alvo_likes and cfg.likes_today < alvo_likes:        # fase >= 2
            pool += ['like'] * 2
        if alvo_follows and cfg.follows_today < alvo_follows:  # fase >= 3
            pool += ['follow'] * 1
        if not pool:
            continue  # alvo do dia batido — nada a fazer

        tipo = random.choice(pool)
        # Jitter extra (0-4min) para as contas não dispararem juntas no tick.
        warmup_action.apply_async((cfg.id, tipo), countdown=random.randint(0, 240))
        cfg.last_action_at = agora
        cfg.save(update_fields=['last_action_at'])


@shared_task(soft_time_limit=120, time_limit=150)
def warmup_action(config_id, tipo):
    """Micro-tarefa: executa UMA ação de aquecimento. PARA a conta se o IG pedir
    verificação (challenge/feedback) — não insiste, que é o que leva a bloqueio."""
    from django.utils import timezone
    from engine.client import InstagramEngine, WarmupParar
    from .models import WarmupConfig
    try:
        cfg = WarmupConfig.objects.select_related('account').get(id=config_id)
    except WarmupConfig.DoesNotExist:
        return

    a = cfg.account
    if (not cfg.enabled or a.status != 'active' or a.sessao_expirada
            or a.pausada or a.banned_by_admin or not a.tem_sessao_engine):
        return

    # Mira em conteúdo BRASILEIRO: usa o hashtag próprio da conta (nicho BR) se
    # houver; senão sorteia um do pool BR — mantém curtidas/follows no público br.
    import random as _rnd
    from django.conf import settings as _s
    br = getattr(_s, 'WARMUP_HASHTAGS_BR', ['brasil'])
    _ht = (cfg.target_hashtag or '').strip()
    hashtag = _ht if (_ht and _ht.lower() != 'reels') else _rnd.choice(br)
    try:
        done = InstagramEngine(a).warmup_acao(tipo, hashtag=hashtag)
        cfg.likes_today += done.get('likes', 0)
        cfg.follows_today += done.get('follows', 0)
        cfg.views_today += done.get('views', 0)
        cfg.browse_today += done.get('browse', 0)
        rotulo = {'like': 'curtiu 1 post', 'follow': 'seguiu 1 perfil',
                  'view': 'viu posts', 'browse': 'rolou o feed'}
        cfg.last_result = rotulo.get(tipo, tipo)
        cfg.last_run = timezone.now()
        cfg.save(update_fields=['likes_today', 'follows_today', 'views_today',
                                'browse_today', 'last_result', 'last_run'])
    except WarmupParar as e:
        # Sinal forte do IG: PARA o aquecimento desta conta.
        cfg.enabled = False
        cfg.last_result = (f'Parado: o Instagram pediu verificação ({str(e)[:70]}). '
                           'Religue a conta e reative o aquecimento com calma.')
        cfg.last_run = timezone.now()
        cfg.save(update_fields=['enabled', 'last_result', 'last_run'])
        logger.warning('[WARMUP] parado @%s: %s', a.ig_username, str(e)[:150])
    except Exception as e:
        cfg.last_result = f'Erro: {str(e)[:160]}'
        cfg.last_run = timezone.now()
        cfg.save(update_fields=['last_result', 'last_run'])


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


# =============================================================================
# Renovação automática do token da API oficial (Instagram long-lived, ~60 dias)
# =============================================================================
@shared_task
def refresh_meta_tokens():
    """Beat: renova os long-lived tokens da API oficial ANTES de vencerem. O IG
    permite dar refresh num token VÁLIDO com >24h de idade e devolve outro de 60
    dias (graph.instagram.com/refresh_access_token, grant_type=ig_refresh_token).
    Sem isso, o token vence sozinho e a conta cai — é a causa do acúmulo de
    contas só-token em 'erro'. Só toca em quem está perto de vencer (ou validade
    desconhecida/legado). É HTTP puro por token: roda na fila leve (painel)."""
    import requests
    from django.utils import timezone
    from datetime import timedelta

    limite = timezone.now() + timedelta(days=15)
    # Não insiste em token JÁ inválido (status 'error'/190 ou banida) nem em conta
    # pausada — refresh nesses casos é chamada falha repetida contra o app; eles
    # voltam pelo religar manual do token. Só renova quem está saudável.
    qs = (InstagramAccount.objects.exclude(meta_access_token='')
          .exclude(status__in=['banned', 'error']).filter(pausada=False))
    n_ok = n_fail = n_skip = 0
    for acc in qs:
        # Renova só quando está a <=15 dias de vencer (ou validade desconhecida).
        if acc.meta_token_expira_em and acc.meta_token_expira_em > limite:
            n_skip += 1
            continue
        token = acc.get_meta_token()
        if not token:
            continue
        try:
            r = requests.get(
                'https://graph.instagram.com/refresh_access_token',
                params={'grant_type': 'ig_refresh_token', 'access_token': token},
                timeout=15,
            )
            d = r.json()
            novo = d.get('access_token')
            if r.status_code == 200 and novo:
                acc.set_meta_token(novo)
                acc.set_meta_token_expiry(d.get('expires_in'))
                campos = ['meta_access_token', 'meta_token_expira_em']
                # Token renovado: se a conta havia caído SÓ por token, reativa.
                if acc.status == 'error' and not acc.sessao_expirada:
                    acc.status = 'active'
                    acc.last_error = ''
                    acc.rate_limited_until = None
                    campos += ['status', 'last_error', 'rate_limited_until']
                acc.save(update_fields=campos)
                n_ok += 1
                logger.info("[TOKEN] @%s renovado (vence %s)", acc.ig_username, acc.meta_token_expira_em)
            else:
                # Token não é long-lived-IG, já venceu, ou tipo incompatível:
                # não dá pra renovar sozinho — o dono precisa colar um novo/reconectar.
                n_fail += 1
                logger.info("[TOKEN] refresh nao aplicavel @%s: %s", acc.ig_username, str(d)[:150])
                # Avisa o dono SÓ quando está vencido/vencendo (1x por dia por conta).
                if acc.token_vencido or acc.token_expira_em_breve:
                    try:
                        from apps.notifications.alertas import alertar
                        from django.utils import timezone as _tz
                        venc = 'venceu' if acc.token_vencido else 'está vencendo'
                        alertar(
                            acc.owner, 'conta_caiu',
                            'Token precisa ser renovado',
                            f'@{acc.ig_username}: o token da API oficial {venc}. '
                            'Cole um token novo no card da conta (botão "Atualizar token") '
                            'ou reconecte por OAuth.',
                            chave=f'token:{acc.id}:{_tz.now():%Y%m%d}',
                            nivel='warning', account=acc,
                        )
                    except Exception:
                        pass
        except Exception as e:
            n_fail += 1
            logger.info("[TOKEN] refresh erro @%s: %s", acc.ig_username, str(e)[:120])

    return f"tokens renovados={n_ok} falhas={n_fail} no_prazo={n_skip}"
