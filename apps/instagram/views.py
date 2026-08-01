import json
import logging
import re

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from django.contrib import messages
from django.conf import settings
from django.core import signing
from .models import InstagramAccount, Proxy
import requests
from urllib.parse import urlencode
from .forms import AddInstagramAccountForm
from django.core.cache import cache

from .tasks import (
    connect_by_sessionid,
    web_login_account, claim_login_generation,
)

logger = logging.getLogger('apps.instagram')


def _extract_sessionid(raw):
    """Aceita o valor puro do sessionid ou o cookie inteiro colado."""
    raw = (raw or '').strip()
    if 'sessionid=' in raw:
        m = re.search(r'sessionid=([^;\s]+)', raw)
        if m:
            return m.group(1)
    return raw


def _cors(response):
    # A extensão roda a partir de uma origem chrome-extension://<id>.
    # Autenticamos por token no corpo (não por cookie), então liberar * é seguro.
    response['Access-Control-Allow-Origin'] = '*'
    response['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
    response['Access-Control-Allow-Headers'] = 'Content-Type'
    response['Access-Control-Max-Age'] = '86400'
    return response


def _toast(message, toast_type='info'):
    """Resposta HTMX vazia que só dispara um toast (lido pelo app.js)."""
    resp = HttpResponse(status=204)
    resp['HX-Reswap'] = 'none'
    resp['X-Toast-Message'] = message
    resp['X-Toast-Type'] = toast_type
    return resp


# =============================================================================
# OAuth state (anti-CSRF)
# =============================================================================
# O parâmetro `state` do OAuth é assinado com timestamp e amarrado ao usuário.
# Assim garantimos que o callback que chega veio de um fluxo iniciado por nós
# (e pelo mesmo usuário logado), fechando a brecha de CSRF do state fixo.
_STATE_MAX_AGE = 600  # 10 minutos


def _state_signer():
    key = getattr(settings, 'IG_STATE_SECRET', '') or None
    return signing.TimestampSigner(key=key, salt='ig-oauth-state')


@login_required
def account_list(request):
    accounts = InstagramAccount.objects.filter(owner=request.user).select_related('meta_app', 'pasta')

    # Permite filtrar as contas por app Meta (?app=<id>).
    filtro_app = (request.GET.get('app') or '').strip()
    if filtro_app:
        accounts = accounts.filter(meta_app_id=filtro_app)

    # Filtro por PASTA (?pasta=<id> ou ?pasta=none para "Sem pasta").
    filtro_pasta = (request.GET.get('pasta') or '').strip()
    if filtro_pasta == 'none':
        accounts = accounts.filter(pasta__isnull=True)
    elif filtro_pasta:
        accounts = accounts.filter(pasta_id=filtro_pasta)

    # Agrupa por MODELO (item 1): cada modelo vira um bloco; as sem modelo
    # caem em "Sem modelo". Como no Murphy — conjuntos salvos aparecem separados.
    accounts = accounts.order_by('modelo', 'ig_username')
    grupos = {}
    for acc in accounts:
        grupos.setdefault(acc.modelo or '', []).append(acc)
    # Ordena: modelos nomeados primeiro (alfabético), "Sem modelo" por último.
    grupos_ordenados = [
        {'nome': nome or 'Sem modelo', 'contas': contas, 'sem_nome': not nome}
        for nome, contas in sorted(grupos.items(), key=lambda kv: (kv[0] == '', kv[0].lower()))
    ]

    from django.db.models import Count

    from apps.accounts.models import MetaApp
    meta_apps = MetaApp.objects.filter(owner=request.user).annotate(n_contas=Count('accounts'))

    # Limite a partir do qual um app é "lotado" (mesmo número usado no alerta).
    from apps.notifications.alertas import preferencias
    limite_app = preferencias(request.user).app_lotado_limite or 15

    # Pastas do usuário (para as abas de filtro e para atribuir no card).
    from .models import Pasta
    pastas = Pasta.objects.filter(owner=request.user).annotate(n_contas=Count('contas'))
    sem_pasta = InstagramAccount.objects.filter(owner=request.user, pasta__isnull=True).count()

    form = AddInstagramAccountForm()
    connect_url = request.build_absolute_uri('/instagram/connect-extension/')
    return render(request, 'instagram/list.html', {
        'accounts': accounts,
        'grupos_modelo': grupos_ordenados,
        'meta_apps': meta_apps,
        'filtro_app': filtro_app,
        'pastas': pastas,
        'filtro_pasta': filtro_pasta,
        'sem_pasta': sem_pasta,
        'limite_app': limite_app,
        'form': form,
        'extension_token': request.user.ensure_extension_token(),
        'connect_url': connect_url,
    })


@login_required
def add_account(request):
    if request.method != 'POST':
        return redirect('instagram:list')

    form = AddInstagramAccountForm(request.POST)
    if not form.is_valid():
        return _toast('Preencha usuário e senha corretamente.', 'error')

    username = form.cleaned_data['ig_username'].lstrip('@').strip()

    # unique_together (owner + ig_username) não é validado no form porque
    # 'owner' não faz parte dele — checamos aqui para não estourar IntegrityError.
    if InstagramAccount.objects.filter(owner=request.user, ig_username=username).exists():
        return _toast('Você já adicionou essa conta.', 'warning')

    account = form.save(commit=False)
    account.owner = request.user
    account.ig_username = username
    account.set_ig_password(form.cleaned_data['ig_password'])
    # Chave 2FA (TOTP) opcional: com ela o login gera o código sozinho e passa
    # do 2FA a partir da VPS (o IG aceita login por senha em conta com 2FA).
    seed = (request.POST.get('totp_seed') or '').strip()
    if seed:
        account.set_totp_seed(seed)
    account.save()

    # O login agora usa instagrapi (Android API) com Bypass de 2FA.
    # O servidor bloqueia esperando o código do Redis caso seja solicitado 2FA ou challenge.
    gen = claim_login_generation(account.id)
    logger.info("[CONNECT acc=%s @%s] add_account (login/senha) por user=%s -> despachando web_login_account (gen=%s)",
                account.id, username, request.user.username, gen)
    web_login_account.delay(account.id, gen)

    # Retornar o card da conta (HTMX injeta na lista)
    return render(request, 'instagram/partials/account_card.html', {'account': account})


@login_required
def add_account_session(request):
    """Conecta uma conta importando o cookie `sessionid` do instagram.com.

    O usuário faz login na tela real do Instagram no próprio navegador,
    copia o valor do cookie `sessionid` e cola aqui. Muito mais robusto e
    seguro que enviar usuário/senha para a API privada.
    """
    if request.method != 'POST':
        return redirect('instagram:list')

    sessionid = _extract_sessionid(request.POST.get('sessionid'))

    if not sessionid:
        return _toast('Cole o valor do sessionid.', 'error')

    username = (request.POST.get('ig_username') or '').lstrip('@').strip()
    proxy_url = (request.POST.get('proxy_url') or '').strip()

    # username é opcional aqui — se vier, garantimos que não é duplicado.
    if username and InstagramAccount.objects.filter(owner=request.user, ig_username=username).exists():
        return _toast('Você já adicionou essa conta.', 'warning')

    account = InstagramAccount(
        owner=request.user,
        ig_username=username,
        proxy_url=proxy_url,
        status='connecting',
    )
    # ig_password é NOT NULL/obrigatório no schema; guardamos um placeholder
    # criptografado já que o fluxo de sessão não usa senha.
    account.set_ig_password('__session_login__')
    account.save()

    connect_by_sessionid.delay(account.id, sessionid)

    return render(request, 'instagram/partials/account_card.html', {'account': account})


@csrf_exempt
def connect_extension(request):
    """Endpoint chamado pela extensão de navegador.

    A extensão lê o cookie `sessionid` do instagram.com no navegador do
    usuário e o envia aqui junto do `token` de conexão da conta na plataforma.
    Autenticamos pelo token (não por sessão/cookie), então o endpoint é
    csrf_exempt e responde a preflight CORS.

    Corpo (JSON): {"token": "...", "sessionid": "...", "username": "opcional"}
    """
    if request.method == 'OPTIONS':
        return _cors(HttpResponse(status=204))

    if request.method != 'POST':
        return _cors(JsonResponse({'ok': False, 'error': 'Método não permitido.'}, status=405))

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return _cors(JsonResponse({'ok': False, 'error': 'JSON inválido.'}, status=400))

    token = (payload.get('token') or '').strip()
    sessionid = _extract_sessionid(payload.get('sessionid'))
    username = (payload.get('username') or '').lstrip('@').strip()

    if not token:
        return _cors(JsonResponse({'ok': False, 'error': 'Token de conexão ausente.'}, status=401))
    if not sessionid:
        return _cors(JsonResponse({'ok': False, 'error': 'sessionid não encontrado. Faça login no instagram.com primeiro.'}, status=400))

    user = User.objects.filter(extension_token=token).first()
    if not user:
        return _cors(JsonResponse({'ok': False, 'error': 'Token de conexão inválido. Copie-o novamente na plataforma.'}, status=401))

    # Sem limite de contas por enquanto (ainda não há planos pagos).
    existing = InstagramAccount.objects.filter(owner=user, ig_username=username).first() if username else None

    account = existing or InstagramAccount(owner=user, ig_username=username)
    account.status = 'connecting'
    if not account.ig_password:
        account.set_ig_password('__session_login__')
    try:
        account.save()
    except IntegrityError:
        return _cors(JsonResponse({
            'ok': False,
            'error': 'Já existe uma conexão em andamento. Aguarde alguns segundos e tente de novo.',
        }, status=409))

    connect_by_sessionid.delay(account.id, sessionid)

    return _cors(JsonResponse({
        'ok': True,
        'account_id': account.id,
        'message': 'Sessão recebida! Conectando a conta na plataforma...',
    }))


@login_required
@require_POST
def add_account_meta(request):
    """
    Salva uma conta usando o Token da Meta Graph API (inserido manualmente).
    """
    from django.utils.html import escape

    ig_username = request.POST.get('ig_username', '').strip().lower()
    meta_access_token = request.POST.get('meta_access_token', '').strip()
    ig_user_id = request.POST.get('ig_user_id', '').strip()
    profile_pic_url = (request.POST.get('profile_pic_url') or '').strip()

    logger.info("[CONNECT @%s] add_account_meta por user=%s | token_len=%s | meta_app=%s | ig_user_id=%s | @=%s",
                ig_username or '?', request.user.username, len(meta_access_token),
                (request.POST.get('meta_app') or '') or '(nenhum)', ig_user_id or '(vazio)', ig_username or '(vazio)')

    if not meta_access_token:
        return HttpResponse(
            '<div class="alert alert-danger"><i class="bi bi-exclamation-triangle"></i> O Token do Instagram é obrigatório.</div>'
        )

    # App Meta é OPCIONAL: o token colado JÁ é a credencial usada para publicar
    # (a publicação usa só o access_token). O app (id/secret) só serve para
    # OAuth e para renovar o token automaticamente. Então quem só tem o token
    # conecta normalmente; se o usuário escolher um app dele, vinculamos.
    meta_app = None
    meta_app_id = (request.POST.get('meta_app') or '').strip()
    if meta_app_id:
        from apps.accounts.models import MetaApp
        meta_app = MetaApp.objects.filter(id=meta_app_id, owner=request.user).first()

    # O @ é opcional no import por token: se não vier, usamos o ig_user_id
    # como identificador provisório (a sincronização com a Meta preenche depois).
    if not ig_username:
        ig_username = ig_user_id or f'conta_{meta_access_token[-6:]}' if meta_access_token else ''

    # Cada conta do Instagram é única no sistema: se outro usuário já a
    # cadastrou, os dois publicariam nela ao mesmo tempo — cota da Meta
    # estourada e risco real de bloqueio.
    if ig_user_id.isdigit():
        dona = (InstagramAccount.objects
                .filter(ig_user_id=int(ig_user_id))
                .exclude(owner=request.user).first())
        if dona:
            return HttpResponse(
                '<div class="alert alert-danger"><i class="bi bi-exclamation-triangle"></i> '
                'Esta conta do Instagram já está cadastrada por outro usuário. '
                'Cada conta pertence a um único cadastro.</div>'
            )

    # Sem limite de contas por enquanto (ainda não há planos pagos).
    try:
        acc, _created = InstagramAccount.objects.get_or_create(
            owner=request.user,
            ig_username=ig_username,
            defaults={'status': 'active'},
        )

        acc.set_meta_token(meta_access_token)
        acc.meta_app = meta_app
        if ig_user_id.isdigit():
            acc.ig_user_id = int(ig_user_id)
        if profile_pic_url:
            acc.profile_pic_url = profile_pic_url[:1000]
        acc.status = 'active'
        acc.save()

        # Valida o token na Meta (preenche user_id/@/seguidores/foto).
        # Se falhar, o usuário PRECISA saber o motivo exato — mostramos a
        # mensagem da Meta e não deixamos uma conta quebrada para trás
        # (removemos a que acabamos de criar; se já existia, mantemos e o card
        # cai depois pelo botão de sincronizar).
        try:
            ok, msg = _sync_meta_account(acc)
        except Exception as e:
            ok, msg = False, str(e)

        logger.info("[CONNECT acc=%s @%s] add_account_meta sync -> ok=%s msg=%s status=%s",
                    acc.id, acc.ig_username, ok, (msg or '')[:200], acc.status)

        if not ok:
            if _created:
                acc.delete()
            return HttpResponse(
                '<div class="alert alert-danger"><i class="bi bi-exclamation-triangle"></i> '
                f'Não foi possível validar o token: <strong>{escape(msg)}</strong>.<br>'
                'Gere um token novo em <strong>Instagram → Gerar tokens de acesso</strong> '
                '(ou no Graph API Explorer), confirme que a conta é profissional/Business '
                'e que o token não expirou, e tente de novo.</div>'
            )

        # Token válido: sobe o card da conta conectada.
        return render(request, 'instagram/partials/account_card.html', {'account': acc})

    except Exception as e:
        logger.exception('add_account_meta falhou (username=%s, ig_user_id=%s)', ig_username, ig_user_id)
        return HttpResponse(
            f'<div class="alert alert-danger"><i class="bi bi-exclamation-triangle"></i> Erro ao salvar Token: {escape(str(e))}</div>'
        )


# Versão da Graph API do Instagram (doc oficial usa graph.instagram.com/v23.0).
IG_API_VERSION = 'v23.0'

# Trechos que, na resposta da Meta, indicam conta indisponível/desabilitada
# (e não apenas token vencido). Nota honesta: a Meta NÃO expõe um sinal
# explícito de "banido"/shadowban — isto é a melhor inferência possível.
INDICIOS_DE_BAN = (
    'account has been disabled',
    'account is disabled',
    'account has been deleted',
    'user not found',
    'does not exist',
    'deactivated',
    'temporarily blocked',
    'restricted',
    'not eligible',
)


def _classificar_falha(erro, msg):
    """Decide entre 'banned' (conta indisponível) e 'error' (ex.: token vencido)."""
    texto = f"{msg} {erro.get('error_user_msg', '')}".lower()
    if any(t in texto for t in INDICIOS_DE_BAN):
        return 'banned'
    return 'error'


def _sync_meta_account(account):
    """Busca dados da conta a partir do token e grava. Baseado na doc oficial:
    GET graph.instagram.com/{v}/me?fields=user_id,username,account_type,...
    IMPORTANTE: usa-se `user_id` (Instagram professional account ID) para
    publicar — NÃO o `id` (app-scoped). Fetch em 2 etapas: identidade (sempre)
    + métricas (best-effort, para não quebrar se faltar escopo). Retorna (ok, msg)."""
    token = account.get_meta_token()
    if not token:
        return False, 'Conta sem token Meta.'

    # Guarda o estado ANTES: se a conta estava caída/em cooldown e a sincronização
    # der certo, é porque o app voltou — aí retomamos a fila travada (ver o fim).
    estava_caida = account.status != 'active' or bool(account.rate_limited_until)
    cooldown_antigo = account.rate_limited_until

    base = f"https://graph.instagram.com/{IG_API_VERSION}/me"

    # Etapa 1 — identidade (campos presentes em qualquer token de IG Login).
    try:
        resp = requests.get(base, params={'fields': 'user_id,username,account_type', 'access_token': token}, timeout=20)
        data = resp.json()
    except Exception as e:
        account.last_error = f'Falha ao contatar a Meta: {e}'
        account.save(update_fields=['last_error'])
        return False, str(e)

    if 'error' in data:
        erro = data.get('error') or {}
        msg = erro.get('message', 'Token inválido ou expirado.')
        logger.warning('Sync Meta falhou (acc=%s): %s', account.id, erro)
        account.status = _classificar_falha(erro, msg)
        account.last_error = f'Meta: {msg}'
        account.save(update_fields=['status', 'last_error'])
        return False, msg

    uid = str(data.get('user_id') or data.get('id') or '')
    logger.info('Sync Meta OK (acc=%s): user_id=%s username=%s', account.id, uid, data.get('username'))
    if uid.isdigit():
        account.ig_user_id = int(uid)

    new_username = (data.get('username') or '').strip()
    # Só renomeia se não colidir com outra conta do mesmo dono (unique_together).
    if new_username and not InstagramAccount.objects.filter(
        owner=account.owner, ig_username=new_username
    ).exclude(id=account.id).exists():
        account.ig_username = new_username

    # Etapa 2 — métricas/foto (best-effort: se faltar escopo, ignora e segue).
    try:
        r2 = requests.get(base, params={
            'fields': 'name,profile_picture_url,followers_count,follows_count,media_count',
            'access_token': token,
        }, timeout=20)
        d2 = r2.json()
        if 'error' not in d2:
            if d2.get('name'):
                account.full_name = d2['name'][:255]
            if d2.get('profile_picture_url'):
                # Trunca por segurança: URLs de CDN do IG são longas.
                account.profile_pic_url = d2['profile_picture_url'][:1000]
            if d2.get('followers_count') is not None:
                account.followers_count = d2['followers_count']
            if d2.get('follows_count') is not None:
                account.following_count = d2['follows_count']
            if d2.get('media_count') is not None:
                account.posts_count = d2['media_count']
    except Exception:
        pass

    # Cota real de publicação (janela de 24h da Meta).
    try:
        if account.ig_user_id:
            q = requests.get(
                f"https://graph.instagram.com/{IG_API_VERSION}/{account.ig_user_id}/content_publishing_limit",
                params={'fields': 'config,quota_usage', 'access_token': token}, timeout=15,
            ).json()
            dados = (q.get('data') or [{}])[0]
            if 'quota_usage' in dados:
                account.quota_usage = dados.get('quota_usage', 0)
                account.quota_total = (dados.get('config') or {}).get('quota_total', 0)
                from django.utils import timezone as _tz
                account.quota_checked_at = _tz.now()
    except Exception:
        pass

    account.status = 'active'
    account.last_error = ''
    # Se a sincronização passou, o token voltou a valer: limpa o cooldown que
    # tinha sido posto por app inválido, para a fila retomar na hora.
    account.rate_limited_until = None
    account.save()

    # Voltou da restrição: puxa os posts que ficaram travados de volta para
    # AGORA, para publicarem já. Só os empurrados PELO cooldown (scheduled_for
    # <= cooldown antigo) — os que o usuário agendou de propósito para depois
    # do cooldown ficam intactos.
    if estava_caida and cooldown_antigo:
        try:
            from django.utils import timezone as _tz
            from apps.publisher.models import ScheduledPost
            agora = _tz.now()
            (ScheduledPost.objects.filter(account=account, status='queued',
                                          scheduled_for__gt=agora,
                                          scheduled_for__lte=cooldown_antigo)
             .update(scheduled_for=agora))
        except Exception:
            pass
    return True, 'ok'


@login_required
@require_POST
def update_account_limit(request, account_id):
    """Ajusta o teto diário de publicações da conta (0 = sem limite)."""
    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    try:
        account.daily_post_limit = max(int(request.POST.get('daily_post_limit', 100)), 0)
    except (TypeError, ValueError):
        account.daily_post_limit = 100
    account.save(update_fields=['daily_post_limit'])
    return render(request, 'instagram/partials/account_card.html', {'account': account})


@login_required
@require_POST
def change_account_app(request, account_id):
    """Troca o app Meta de uma conta (sem apagar e recadastrar).

    Resolve o caso real: o usuário queria mover contas de um app para outro e,
    como o cadastro sempre repunha no app ativo, elas não iam para o app novo.
    Só aceita um app do próprio usuário — nunca de outro.
    """
    from apps.accounts.models import MetaApp

    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    app_pk = (request.POST.get('meta_app') or '').strip()
    if app_pk:
        app = _get_user_meta_app(request.user, app_pk)
        if app is None:
            return _toast('App inválido.', 'error')
        account.meta_app = app
        account.save(update_fields=['meta_app'])
    return render(request, 'instagram/partials/account_card.html', {
        'account': account,
        'meta_apps': MetaApp.objects.filter(owner=request.user),
    })


@login_required
@require_POST
def toggle_forcar(request, account_id):
    """Liga/desliga o modo forçado: publica mesmo com o limite batido.

    Ao ligar, também zera o cooldown atual — senão a conta continuaria parada
    até o horário antigo mesmo com o modo ligado.
    """
    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    account.ignorar_limites = not account.ignorar_limites
    campos = ['ignorar_limites']
    if account.ignorar_limites:
        # Ao FORÇAR: zera cooldown, tira a pausa e puxa os posts que tinham
        # sido adiados pelo limite de volta para agora — a fila retoma na hora.
        if account.rate_limited_until:
            account.rate_limited_until = None
            campos.append('rate_limited_until')
        if account.pausada:
            account.pausada = False
            campos.append('pausada')
        _subir_fila_agora(account)
    account.save(update_fields=campos)
    return render(request, 'instagram/partials/account_card.html', {'account': account})


def _subir_fila_agora(account):
    """Puxa os posts adiados desta conta de volta para agora (retoma a fila)."""
    from django.utils import timezone
    from apps.publisher.models import ScheduledPost
    agora = timezone.now()
    (ScheduledPost.objects.filter(account=account, status='queued',
                                  scheduled_for__gt=agora)
     .update(scheduled_for=agora))


@login_required
@require_POST
def reativar_conta(request, account_id):
    """Reativa a conta e sobe a fila: tira pausa, zera cooldown e traz os posts
    adiados para agora. É o 'ativar conta novamente' quando ela limitou."""
    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    account.pausada = False
    account.rate_limited_until = None
    account.save(update_fields=['pausada', 'rate_limited_until'])
    _subir_fila_agora(account)
    return render(request, 'instagram/partials/account_card.html', {'account': account})


@login_required
@require_POST
def update_token(request, account_id):
    """Atualiza SÓ o token de uma conta que caiu (sem refazer a conexão). Valida
    o token novo na Meta e, se válido, reativa a conta e retoma a fila."""
    import requests as _rq
    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    token = (request.POST.get('meta_access_token') or '').strip()
    if not token:
        return render(request, 'instagram/partials/account_card.html', {'account': account})

    account.set_meta_token(token)
    try:
        ig_id = account.ig_user_id or 'me'
        r = _rq.get(
            f'https://graph.instagram.com/v23.0/{ig_id}',
            params={'fields': 'id,username', 'access_token': token}, timeout=12,
        )
        data = r.json()
        if r.status_code == 200 and 'id' in data:
            account.status = 'active'
            account.last_error = ''
            account.rate_limited_until = None
            if data.get('username'):
                account.ig_username = data['username']
            if not account.ig_user_id and str(data.get('id', '')).isdigit():
                account.ig_user_id = int(data['id'])
            _subir_fila_agora(account)
        else:
            account.status = 'error'
            account.last_error = f"Token recusado pela Meta: {data.get('error', data)}"
    except Exception as e:
        account.status = 'error'
        account.last_error = f'Falha ao validar o token novo: {str(e)[:150]}'
    account.save()
    return render(request, 'instagram/partials/account_card.html', {'account': account})


@login_required
@require_POST
def update_session(request, account_id):
    """Reconecta uma conta de SESSÃO que expirou colando SÓ o novo sessionid —
    sem refazer o cadastro. Revalida em background pela engine (fila do braço)."""
    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    sessionid = _extract_sessionid(request.POST.get('sessionid'))
    if not sessionid:
        return _toast('Cole o valor do novo sessionid.', 'error')

    account.status = 'connecting'
    account.last_error = ''
    account.rate_limited_until = None
    account.save(update_fields=['status', 'last_error', 'rate_limited_until'])

    connect_by_sessionid.delay(account.id, sessionid)
    return render(request, 'instagram/partials/account_card.html', {'account': account})


@login_required
@require_POST
def bulk_accounts(request):
    """Ações em massa nas contas SELECIONADAS: ativar (reativa + sobe a fila),
    pausar ou remover. Uma única ação para várias contas de uma vez."""
    ids = request.POST.getlist('account_ids')
    acao = (request.POST.get('acao') or '').strip()
    qs = InstagramAccount.objects.filter(owner=request.user, id__in=ids)
    n = qs.count()
    if not n:
        messages.error(request, 'Nenhuma conta selecionada.')
        return redirect('instagram:list')

    if acao == 'ativar':
        for a in qs:
            a.pausada = False
            a.rate_limited_until = None
            a.save(update_fields=['pausada', 'rate_limited_until'])
            try:
                _subir_fila_agora(a)
            except Exception:
                pass
        messages.success(request, f'{n} conta(s) ativadas e fila liberada.')
    elif acao == 'pausar':
        qs.update(pausada=True)
        messages.success(request, f'{n} conta(s) pausadas.')
    elif acao == 'remover':
        qs.delete()
        messages.success(request, f'{n} conta(s) removidas.')
    else:
        messages.error(request, 'Ação inválida.')
    return redirect('instagram:list')


@login_required
@require_POST
def criar_pasta(request):
    """Cria uma pasta para organizar as contas."""
    from .models import Pasta
    nome = (request.POST.get('name') or '').strip()[:80]
    if nome:
        Pasta.objects.get_or_create(owner=request.user, name=nome)
    return redirect('instagram:list')


@login_required
@require_POST
def delete_pasta(request, pasta_id):
    """Apaga a pasta (as contas ficam sem pasta, não são excluídas)."""
    from .models import Pasta
    pasta = get_object_or_404(Pasta, id=pasta_id, owner=request.user)
    pasta.delete()  # contas ficam com pasta=NULL (SET_NULL)
    return redirect('instagram:list')


@login_required
@require_POST
def set_pasta(request, account_id):
    """Move a conta para uma pasta (ou tira, com pasta vazia)."""
    from .models import Pasta
    from apps.accounts.models import MetaApp
    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    pasta_id = (request.POST.get('pasta') or '').strip()
    if pasta_id:
        account.pasta = get_object_or_404(Pasta, id=pasta_id, owner=request.user)
    else:
        account.pasta = None
    account.save(update_fields=['pasta'])
    return render(request, 'instagram/partials/account_card.html', {
        'account': account,
        'meta_apps': MetaApp.objects.filter(owner=request.user),
    })


@login_required
@require_POST
def save_modelo(request, account_id):
    """Grava o 'modelo' (grupo) da conta — para separar contas no painel."""
    from apps.accounts.models import MetaApp

    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    account.modelo = (request.POST.get('modelo') or '').strip()[:60]
    account.save(update_fields=['modelo'])
    return render(request, 'instagram/partials/account_card.html', {
        'account': account,
        'meta_apps': MetaApp.objects.filter(owner=request.user),
    })


@login_required
@require_POST
def toggle_pausada(request, account_id):
    """Pausa/retoma a fila DESTA conta. As outras contas seguem publicando."""
    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    account.pausada = not account.pausada
    account.save(update_fields=['pausada'])
    return render(request, 'instagram/partials/account_card.html', {'account': account})


@login_required
@require_POST
def sync_meta_account(request, account_id):
    """Sincroniza uma conta específica com a Meta (HTMX → devolve o card)."""
    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    _sync_meta_account(account)
    return render(request, 'instagram/partials/account_card.html', {'account': account})


@login_required
def sync_all_meta(request):
    """Sincroniza todas as contas com token Meta do usuário."""
    accounts = InstagramAccount.objects.filter(owner=request.user).exclude(meta_access_token='')
    ok = 0
    for acc in accounts:
        success, _msg = _sync_meta_account(acc)
        if success:
            ok += 1
    messages.success(request, f'{ok} de {accounts.count()} conta(s) sincronizada(s) com a Meta.')
    return redirect('instagram:list')


@login_required
@require_POST
def regenerate_extension_token(request):
    """Gera um novo token de conexão (invalida o anterior)."""
    request.user.rotate_extension_token()
    return _toast('Novo token gerado. Atualize-o na extensão.', 'success')


@login_required
def account_status_partial(request, account_id):
    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    return render(request, 'instagram/partials/account_card.html', {'account': account})


@login_required
def submit_challenge(request, account_id):
    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    if request.method == 'POST':
        code = (request.POST.get('code') or '').strip()
        if code:
            account.status = 'connecting'
            account.save(update_fields=['status'])
            # Entrega o código à task de login web que está aguardando (Playwright).
            # A chave espelha engine/tasks -> _code_cache_key(account_id).
            cache.set(f'ig_login_code:{account_id}', code, timeout=300)
    return render(request, 'instagram/partials/account_card.html', {'account': account})


@login_required
@require_POST
def resend_challenge(request, account_id):
    """Reenvia o código / recomeça o login.

    Útil quando o código não chegou (e-mail/SMS do checkpoint) ou a tentativa
    anterior expirou. Reserva uma nova geração (o que faz a task antiga fechar
    o navegador e parar de escrever status) e dispara um login web novo — no
    checkpoint isso reenvia o código; no 2FA, apenas reinicia a espera.
    """
    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)

    # Limpa qualquer código velho pendente e reinicia o estado visível.
    cache.delete(f'ig_login_code:{account_id}')
    account.status = 'connecting'
    account.last_error = ''
    account.save(update_fields=['status', 'last_error'])

    gen = claim_login_generation(account.id)
    web_login_account.delay(account.id, gen)

    return render(request, 'instagram/partials/account_card.html', {'account': account})


@login_required
@require_POST
def remove_account(request, account_id):
    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    account.delete()
    return redirect('instagram:list')


def _get_user_meta_app(user, app_pk):
    """Busca um MetaApp do usuário pelo id (ou None)."""
    if not app_pk:
        return None
    from apps.accounts.models import MetaApp
    return MetaApp.objects.filter(id=app_pk, owner=user).first()


def _resolver_app(user, escolhido=None):
    """App Meta a vincular na conta. Só resolve quando NÃO há ambiguidade.

    Cada conta pertence a um único app. Se o usuário tem mais de um app e não
    escolheu, adivinhar (usando o "app ativo") vincularia a conta ao app
    errado — o token não bate e a conta cai. Nesse caso devolvemos None e
    quem chamou avisa o usuário.
    """
    from apps.accounts.models import MetaApp

    if escolhido is not None:
        return escolhido
    apps_do_usuario = list(MetaApp.objects.filter(owner=user))
    if len(apps_do_usuario) == 1:
        return apps_do_usuario[0]
    return None


def _meta_credentials(user, app=None):
    """Credenciais Meta DESTE usuário. Nunca as de outro.

    Ordem:
      1) app informado (já validado como sendo do próprio usuário)
      2) app ativo do usuário
      3) campos legados no próprio usuário

    Não existe passo 4. Antes havia um fallback para META_APP_ID/SECRET do
    .env — um app ÚNICO compartilhado por todos os usuários. Bastava alguém
    ficar sem app próprio para passar a usar o app de outra pessoa (na
    prática, o do sistema), misturando as conexões. Sem credencial própria,
    devolvemos vazio e a tela pede para cadastrar o app.
    """
    app = app or user.get_active_meta_app()
    if app and app.is_complete:
        # Cinto de segurança: um app só serve ao seu dono.
        if app.owner_id != user.id:
            return '', ''
        # Fluxo OAuth do Instagram exige o Instagram App ID/secret (não o do
        # Facebook) — senão dá "Invalid platform app".
        return app.oauth_credentials()

    legacy_id = (getattr(user, 'meta_app_id', '') or '').strip()
    if legacy_id:
        return legacy_id, user.get_meta_app_secret()

    return '', ''


@login_required
def oauth_url(request):
    """Retorna a URL OAuth. Aceita ?app=<id> para escolher o app Meta."""
    # Aceita ?app= ou ?meta_app= (o seletor do modal usa "meta_app").
    escolhido_pk = (request.GET.get('app') or request.GET.get('meta_app') or '').strip()
    chosen = _get_user_meta_app(request.user, escolhido_pk)
    if chosen is None:
        chosen = request.user.get_active_meta_app()

    app_id, _secret = _meta_credentials(request.user, chosen)
    redirect_uri = getattr(settings, 'META_REDIRECT_URI', '')

    if not app_id:
        return HttpResponse(
            '<div class="alert alert-danger">Configure o <strong>App ID</strong> do seu app Meta em '
            '<a href="/accounts/settings/" class="alert-link">Configurações</a> antes de conectar.</div>'
        )

    # SÓ o que a gente realmente usa. Cada permissão avançada a mais aumenta o
    # atrito da análise/Verificação da Empresa na Meta (e polui a tela de
    # consentimento do dono). Não usamos mensagens/comentários da API.
    scopes = [
        'instagram_business_basic',          # ler perfil/username (obrigatório)
        'instagram_business_content_publish',  # publicar feed/reels/story
        'instagram_business_manage_insights',  # métricas (views) no painel
    ]

    # `state` assinado carrega usuário + app escolhido, para o callback saber
    # a qual app vincular a conta (anti-CSRF via assinatura + timestamp).
    state = _state_signer().sign(f"{request.user.id}:{chosen.id if chosen else ''}")

    params = {
        'client_id': app_id,
        'redirect_uri': redirect_uri,
        'scope': ','.join(scopes),
        'response_type': 'code',
        'state': state,
    }

    auth_url = f"https://www.instagram.com/oauth/authorize?{urlencode(params)}"

    logger.info(
        "[CONNECT] oauth_url gerado user=%s | app=%s(id=%s) | client_id=%s | redirect_uri=%s | scope=%s",
        request.user.username,
        (chosen.name if chosen else '(nenhum)'),
        (chosen.id if chosen else '-'),
        app_id or '(vazio)',
        redirect_uri,
        ','.join(scopes),
    )

    return render(request, 'instagram/partials/oauth_link.html', {'auth_url': auth_url})


@login_required
def oauth_callback(request):
    """Callback redirecionado pelo Instagram após a autorização OAuth."""
    code = request.GET.get('code')
    error = request.GET.get('error')
    state = request.GET.get('state', '')

    if error:
        messages.error(request, f"Erro na autorização Meta: {error}")
        return redirect('instagram:list')

    if not code:
        messages.error(request, "Nenhum código de autorização recebido.")
        return redirect('instagram:list')

    # Valida o `state` assinado: precisa ter sido gerado por nós, não ter
    # expirado e pertencer ao usuário logado (proteção anti-CSRF).
    try:
        signed_user = _state_signer().unsign(state, max_age=_STATE_MAX_AGE)
    except signing.SignatureExpired:
        messages.error(request, "A autorização expirou. Tente conectar novamente.")
        return redirect('instagram:list')
    except signing.BadSignature:
        messages.error(request, "State OAuth inválido. Reinicie a conexão pela plataforma.")
        return redirect('instagram:list')

    # O state carrega "<user_id>:<meta_app_id>".
    partes = signed_user.split(':', 1)
    signed_user_id = partes[0]
    chosen_app_pk = partes[1] if len(partes) > 1 else ''

    if signed_user_id != str(request.user.id):
        messages.error(request, "Este fluxo de conexão não pertence à sua conta.")
        return redirect('instagram:list')

    # Usa as MESMAS credenciais do app escolhido na geração da URL.
    chosen_app = _get_user_meta_app(request.user, chosen_app_pk)
    app_id, app_secret = _meta_credentials(request.user, chosen_app)
    redirect_uri = getattr(settings, 'META_REDIRECT_URI', '')
    logger.info("[CONNECT] oauth_callback user=%s | app_id=%s | redirect_uri=%s | code_len=%s",
                request.user.username, app_id or '(vazio)', redirect_uri, len(code or ''))

    # 1. Trocar código por short-lived token
    url = "https://api.instagram.com/oauth/access_token"
    payload = {
        'client_id': app_id,
        'client_secret': app_secret,
        'grant_type': 'authorization_code',
        'redirect_uri': redirect_uri,
        'code': code,
    }

    try:
        response = requests.post(url, data=payload, timeout=15)
        data = response.json()

        if 'access_token' not in data:
            logger.warning("[CONNECT] oauth_callback FALHA troca de codigo (HTTP %s): %s",
                           response.status_code, str(data)[:400])
            messages.error(request, f"Falha ao obter token (curto): {data.get('error_message', str(data))}")
            return redirect('instagram:list')

        short_token = data['access_token']
        user_id = str(data.get('user_id', ''))
        logger.info("[CONNECT] oauth_callback short-token OK | user_id=%s", user_id or '(vazio)')

        # 2. Trocar short por long-lived
        ll_url = "https://graph.instagram.com/access_token"
        ll_params = {
            'grant_type': 'ig_exchange_token',
            'client_secret': app_secret,
            'access_token': short_token,
        }

        ll_response = requests.get(ll_url, params=ll_params, timeout=15)
        ll_data = ll_response.json()

        access_token = ll_data.get('access_token', short_token)

        # 3. Buscar Perfil Instagram
        me_url = f"https://graph.instagram.com/v23.0/{user_id}" if user_id else "https://graph.instagram.com/v23.0/me"
        me_params = {
            'fields': 'id,username,name,followers_count,media_count,profile_picture_url',
            'access_token': access_token,
        }

        profile_data = {}
        me_response = requests.get(me_url, params=me_params, timeout=15)
        if me_response.status_code == 200:
            profile_data = me_response.json()

        if not profile_data.get('username') and not user_id:
            me_response_fb = requests.get("https://graph.instagram.com/v23.0/me", params=me_params, timeout=15)
            if me_response_fb.status_code == 200:
                profile_data = me_response_fb.json()

        username = profile_data.get('username', user_id)
        if not username:
            messages.error(request, "Não foi possível obter o username da Meta.")
            return redirect('instagram:list')

        # Conta única no sistema: dois donos publicando na mesma conta
        # estouram a cota da Meta e levam a bloqueio.
        if user_id.isdigit():
            ja_existe = (InstagramAccount.objects
                         .filter(ig_user_id=int(user_id))
                         .exclude(owner=request.user).exists())
            if ja_existe:
                messages.error(
                    request,
                    f'A conta @{username} já está cadastrada por outro usuário. '
                    'Cada conta do Instagram pertence a um único cadastro.')
                return redirect('instagram:list')

        # 4. Salvar / Atualizar Conta
        account, _created = InstagramAccount.objects.get_or_create(
            owner=request.user,
            ig_username=username,
            defaults={'status': 'active'},
        )

        account.set_meta_token(access_token)
        # Guarda a validade (long-lived ~60 dias) para o beat renovar antes de vencer.
        account.set_meta_token_expiry(ll_data.get('expires_in'))
        # Vincula a conta ao app pelo qual ela foi REALMENTE conectada — é o
        # app que assinou o state e trocou o código pelo token.
        account.meta_app = _resolver_app(request.user, chosen_app)
        if user_id.isdigit():
            account.ig_user_id = int(user_id)
        account.status = 'active'
        account.save()

        via = f" (app: {account.meta_app.name})" if account.meta_app else ""
        logger.info("[CONNECT acc=%s @%s] oauth_callback OK -> conta conectada%s",
                    account.id, username, via)
        messages.success(request, f"Conta @{username} conectada com sucesso via Meta API!{via}")

    except Exception as e:
        logger.exception("[CONNECT] oauth_callback ERRO interno user=%s: %s", request.user.username, e)
        messages.error(request, f"Erro interno na conexão OAuth: {str(e)}")

    return redirect('instagram:list')


@login_required
def profile(request):
    account = InstagramAccount.objects.filter(owner=request.user).first()
    return render(request, 'instagram/profile.html', {'account': account})


@login_required
def proxies(request):
    proxy_list = Proxy.objects.filter(owner=request.user)
    return render(request, 'instagram/proxies.html', {'proxies': proxy_list})


@login_required
@require_POST
def add_proxy(request):
    ip_address = (request.POST.get('ip_address') or '').strip()
    port = (request.POST.get('port') or '').strip()
    if not ip_address or not port.isdigit():
        messages.error(request, 'Informe um IP/host e uma porta válida.')
        return redirect('instagram:proxies')

    Proxy.objects.create(
        owner=request.user,
        ip_address=ip_address,
        port=int(port),
        username=(request.POST.get('username') or '').strip(),
        password=(request.POST.get('password') or '').strip(),
        protocol=(request.POST.get('protocol') or 'http').strip(),
        is_active=True,
    )
    messages.success(request, 'Proxy adicionado com sucesso.')
    return redirect('instagram:proxies')


@login_required
@require_POST
def toggle_proxy(request, proxy_id):
    proxy = get_object_or_404(Proxy, id=proxy_id, owner=request.user)
    proxy.is_active = not proxy.is_active
    proxy.save(update_fields=['is_active'])
    return redirect('instagram:proxies')


@login_required
@require_POST
def delete_proxy(request, proxy_id):
    proxy = get_object_or_404(Proxy, id=proxy_id, owner=request.user)
    proxy.delete()
    return redirect('instagram:proxies')


# =============================================================================
# Onda 4 — Aquecimento (warm-up) de contas
# =============================================================================
@login_required
def warmup(request):
    from .models import WarmupConfig
    accounts = InstagramAccount.objects.filter(owner=request.user)
    configs = {c.account_id: c for c in WarmupConfig.objects.filter(owner=request.user)}
    rows = [{'account': acc, 'cfg': configs.get(acc.id)} for acc in accounts]
    return render(request, 'instagram/warmup.html', {
        'rows': rows,
        'intensity_choices': WarmupConfig.INTENSITY_CHOICES,
    })


@login_required
@require_POST
def warmup_save(request, account_id):
    from .models import WarmupConfig
    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    cfg, _ = WarmupConfig.objects.get_or_create(account=account, defaults={'owner': request.user})
    cfg.owner = request.user
    cfg.enabled = request.POST.get('enabled') == 'on'
    cfg.intensity = request.POST.get('intensity', 'low')
    cfg.target_hashtag = (request.POST.get('target_hashtag') or 'reels').lstrip('#').strip() or 'reels'
    cfg.save()
    messages.success(request, f'Aquecimento de @{account.ig_username} atualizado.')
    return redirect('instagram:warmup')


# =============================================================================
# Onda 4 — Edição de perfil em massa (bio/nome/link/foto)
# =============================================================================
@login_required
def bulk_edit(request):
    if request.method == 'POST':
        from django.core.files.storage import default_storage
        from .tasks import bulk_edit_profiles

        account_ids = request.POST.getlist('accounts')
        if not account_ids:
            messages.error(request, 'Selecione ao menos uma conta.')
            return redirect('instagram:bulk_edit')

        full_name = (request.POST.get('full_name') or '').strip()
        biography = request.POST.get('biography')
        external_url = (request.POST.get('external_url') or '').strip()

        # Salva a foto e passa o NOME relativo (o braço baixa a cópia local por
        # URL — passar o path local do painel não funcionava no setup 2 máquinas).
        picture_name = None
        if 'picture' in request.FILES:
            from apps.core_utils import nome_seguro
            f = request.FILES['picture']
            f.name = nome_seguro(f.name)
            picture_name = default_storage.save(f'profile_pics/{f.name}', f)

        to_creator = request.POST.get('to_creator') == 'on'

        # Garante que as contas são do usuário.
        owned_ids = list(
            InstagramAccount.objects.filter(id__in=account_ids, owner=request.user).values_list('id', flat=True)
        )
        bulk_edit_profiles.delay(owned_ids, full_name, biography, external_url, picture_name, to_creator)
        extra = ' + converter p/ Criador' if to_creator else ''
        messages.success(request, f'Edição de perfil{extra} disparada para {len(owned_ids)} conta(s). Pode levar alguns minutos.')
        return redirect('instagram:bulk_edit')

    accounts = InstagramAccount.objects.filter(owner=request.user)
    return render(request, 'instagram/bulk_edit.html', {'accounts': accounts})


@login_required
def stories_ativos(request):
    """Painel dos stories ATIVOS por conta (janela de 24h), pela API oficial.

    A Meta expõe o edge /{ig-user-id}/stories, que devolve os stories ainda no
    ar. Aqui juntamos por conta, com cache curto para não pesar (a página pode
    recarregar via HTMX). Não dá para apagar story pela API — só abrir o
    permalink e remover manualmente no Instagram.
    """
    from datetime import timedelta

    from django.core.paginator import Paginator
    from django.utils import timezone

    todas = (InstagramAccount.objects.filter(owner=request.user, status='active')
             .exclude(meta_access_token='').order_by('modelo', 'ig_username'))

    modo = (request.GET.get('modo') or 'ativos').strip()
    conta_atual = (request.GET.get('account') or '').strip()

    # ── INATIVOS: stories já expirados (>24h) do NOSSO banco ──────────
    # A Meta só devolve os ativos pelo edge /stories; os expirados a gente
    # mostra pelo que já publicou (post_type STORY), com a mídia guardada.
    if modo == 'inativos':
        from apps.publisher.models import ScheduledPost
        limite = timezone.now() - timedelta(hours=24)
        expirados = (ScheduledPost.objects.filter(owner=request.user, post_type='STORY',
                                                  status='published', published_at__lte=limite)
                     .select_related('account').order_by('-published_at'))
        if conta_atual:
            expirados = expirados.filter(account_id=conta_atual)
        page_obj = Paginator(expirados, 24).get_page(request.GET.get('page'))
        return render(request, 'instagram/partials/stories_ativos.html', {
            'modo': 'inativos',
            'inativos': page_obj,
            'total_inativos': page_obj.paginator.count,
            'total_contas': todas.count(),
            'todas_contas': todas,
            'conta_atual': conta_atual,
            'page_obj': page_obj,
        })

    # ── ATIVOS: pela API oficial (janela de 24h) ─────────────────────
    # Com muitas contas, buscar todas de uma vez pesa — paginamos (8/página)
    # e só buscamos as exibidas. Ao escolher uma conta, mostramos só ela.
    if conta_atual:
        contas_pagina = list(todas.filter(id=conta_atual))
        page_obj = None
    else:
        page_obj = Paginator(todas, 8).get_page(request.GET.get('page'))
        contas_pagina = list(page_obj.object_list)

    def buscar(acc):
        cache_key = f'stories_ativos_{acc.id}'
        stories = cache.get(cache_key)
        if stories is None:
            stories = []
            if acc.ig_user_id:
                try:
                    r = requests.get(
                        f'https://graph.instagram.com/{IG_API_VERSION}/{acc.ig_user_id}/stories',
                        params={'fields': 'id,media_type,media_url,thumbnail_url,permalink,timestamp',
                                'access_token': acc.get_meta_token()},
                        timeout=12,
                    ).json()
                    if 'error' not in r:
                        stories = r.get('data', [])
                except Exception:
                    stories = []
            cache.set(cache_key, stories, 120)  # 2 min
        return stories

    linhas = []
    total_stories = 0
    for acc in contas_pagina:
        stories = buscar(acc)
        total_stories += len(stories)
        linhas.append({'conta': acc, 'stories': stories})

    return render(request, 'instagram/partials/stories_ativos.html', {
        'modo': 'ativos',
        'linhas': linhas,
        'total_stories': total_stories,
        'total_contas': todas.count(),
        'todas_contas': todas,        # para o seletor
        'conta_atual': conta_atual,
        'page_obj': page_obj,
    })
