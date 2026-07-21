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
    accounts = InstagramAccount.objects.filter(owner=request.user)
    form = AddInstagramAccountForm()
    connect_url = request.build_absolute_uri('/instagram/connect-extension/')
    return render(request, 'instagram/list.html', {
        'accounts': accounts,
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
    account.save()

    # O login agora usa instagrapi (Android API) com Bypass de 2FA.
    # O servidor bloqueia esperando o código do Redis caso seja solicitado 2FA ou challenge.
    gen = claim_login_generation(account.id)
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

    # Respeita o limite do plano.
    current = InstagramAccount.objects.filter(owner=user).count()
    existing = InstagramAccount.objects.filter(owner=user, ig_username=username).first() if username else None
    if existing is None and current >= user.max_ig_accounts:
        return _cors(JsonResponse({'ok': False, 'error': f'Limite de {user.max_ig_accounts} contas atingido no seu plano.'}, status=403))

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
    ig_username = request.POST.get('ig_username', '').strip().lower()
    meta_access_token = request.POST.get('meta_access_token', '').strip()
    ig_user_id = request.POST.get('ig_user_id', '').strip()
    profile_pic_url = (request.POST.get('profile_pic_url') or '').strip()

    # O @ é opcional no import por token: se não vier, usamos o ig_user_id
    # como identificador provisório (a sincronização com a Meta preenche depois).
    if not ig_username:
        ig_username = ig_user_id or f'conta_{meta_access_token[-6:]}' if meta_access_token else ''

    if not meta_access_token:
        return HttpResponse(
            '<div class="alert alert-danger"><i class="bi bi-exclamation-triangle"></i> O Token do Instagram é obrigatório.</div>'
        )

    # Verifica limite de contas
    current_count = InstagramAccount.objects.filter(owner=request.user).count()
    already = InstagramAccount.objects.filter(owner=request.user, ig_username=ig_username).exists()
    if not already and current_count >= request.user.max_ig_accounts:
        return HttpResponse(
            '<div class="alert alert-warning"><i class="bi bi-exclamation-triangle"></i> '
            f'Limite do plano atingido (máx {request.user.max_ig_accounts} contas). Assine o PRO.</div>'
        )

    try:
        acc, _created = InstagramAccount.objects.get_or_create(
            owner=request.user,
            ig_username=ig_username,
            defaults={'status': 'active'},
        )

        acc.set_meta_token(meta_access_token)
        if ig_user_id.isdigit():
            acc.ig_user_id = int(ig_user_id)
        if profile_pic_url:
            acc.profile_pic_url = profile_pic_url
        acc.status = 'active'
        acc.save()

        # Sincroniza com a Meta para preencher user_id/@/seguidores/foto.
        # ISOLADO: uma falha aqui NUNCA pode impedir o cadastro nem o render
        # do card — a conta já está salva e pode ser re-sincronizada pelo botão.
        try:
            _sync_meta_account(acc)
        except Exception as e:
            acc.status = 'error'
            acc.last_error = f'Conta salva, mas a sincronização falhou: {e}'
            acc.save(update_fields=['status', 'last_error'])

        # Retorna o card (a conta sempre "sobe", mesmo se o sync falhar)
        return render(request, 'instagram/partials/account_card.html', {'account': acc})

    except Exception as e:
        logger.exception('add_account_meta falhou (username=%s, ig_user_id=%s)', ig_username, ig_user_id)
        return HttpResponse(
            f'<div class="alert alert-danger"><i class="bi bi-exclamation-triangle"></i> Erro ao salvar Token: {str(e)}</div>'
        )


# Versão da Graph API do Instagram (doc oficial usa graph.instagram.com/v23.0).
IG_API_VERSION = 'v23.0'


def _sync_meta_account(account):
    """Busca dados da conta a partir do token e grava. Baseado na doc oficial:
    GET graph.instagram.com/{v}/me?fields=user_id,username,account_type,...
    IMPORTANTE: usa-se `user_id` (Instagram professional account ID) para
    publicar — NÃO o `id` (app-scoped). Fetch em 2 etapas: identidade (sempre)
    + métricas (best-effort, para não quebrar se faltar escopo). Retorna (ok, msg)."""
    token = account.get_meta_token()
    if not token:
        return False, 'Conta sem token Meta.'

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
        msg = (data.get('error') or {}).get('message', 'Token inválido ou expirado.')
        logger.warning('Sync Meta falhou (acc=%s): %s', account.id, data.get('error'))
        account.status = 'error'
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
                account.full_name = d2['name']
            if d2.get('profile_picture_url'):
                account.profile_pic_url = d2['profile_picture_url']
            if d2.get('followers_count') is not None:
                account.followers_count = d2['followers_count']
            if d2.get('follows_count') is not None:
                account.following_count = d2['follows_count']
            if d2.get('media_count') is not None:
                account.posts_count = d2['media_count']
    except Exception:
        pass

    account.status = 'active'
    account.last_error = ''
    account.save()
    return True, 'ok'


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
def remove_account(request, account_id):
    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    account.delete()
    return redirect('instagram:list')


@login_required
def oauth_url(request):
    """Retorna a URL OAuth e renderiza o HTML para o modal."""
    # Prioriza as credenciais do próprio usuário; cai para as globais do .env.
    app_id = (request.user.meta_app_id or '').strip() or getattr(settings, 'META_APP_ID', '')
    redirect_uri = getattr(settings, 'META_REDIRECT_URI', '')

    if not app_id:
        return HttpResponse(
            '<div class="alert alert-danger">Configure o <strong>App ID</strong> do seu app Meta em '
            '<a href="/accounts/settings/" class="alert-link">Configurações</a> antes de conectar.</div>'
        )

    scopes = [
        'instagram_business_basic',
        'instagram_business_manage_messages',
        'instagram_business_manage_comments',
        'instagram_business_content_publish',
        'instagram_business_manage_insights',
    ]

    # `state` assinado (usuário + timestamp) para validar o callback (anti-CSRF).
    state = _state_signer().sign(str(request.user.id))

    params = {
        'client_id': app_id,
        'redirect_uri': redirect_uri,
        'scope': ','.join(scopes),
        'response_type': 'code',
        'state': state,
    }

    auth_url = f"https://www.instagram.com/oauth/authorize?{urlencode(params)}"

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

    if signed_user != str(request.user.id):
        messages.error(request, "Este fluxo de conexão não pertence à sua conta.")
        return redirect('instagram:list')

    # Mesmas credenciais usadas na geração da URL: as do usuário, com fallback global.
    app_id = (request.user.meta_app_id or '').strip() or getattr(settings, 'META_APP_ID', '')
    app_secret = request.user.get_meta_app_secret() or getattr(settings, 'META_APP_SECRET', '')
    redirect_uri = getattr(settings, 'META_REDIRECT_URI', '')

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
            messages.error(request, f"Falha ao obter token (curto): {data.get('error_message', str(data))}")
            return redirect('instagram:list')

        short_token = data['access_token']
        user_id = str(data.get('user_id', ''))

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

        # 4. Salvar / Atualizar Conta
        account, _created = InstagramAccount.objects.get_or_create(
            owner=request.user,
            ig_username=username,
            defaults={'status': 'active'},
        )

        account.set_meta_token(access_token)
        if user_id.isdigit():
            account.ig_user_id = int(user_id)
        account.status = 'active'
        account.save()

        messages.success(request, f"Conta @{username} conectada com sucesso via Meta API!")

    except Exception as e:
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
def toggle_proxy(request, proxy_id):
    proxy = get_object_or_404(Proxy, id=proxy_id, owner=request.user)
    proxy.is_active = not proxy.is_active
    proxy.save(update_fields=['is_active'])
    return redirect('instagram:proxies')


@login_required
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

        picture_path = None
        if 'picture' in request.FILES:
            name = default_storage.save(f'profile_pics/{request.FILES["picture"].name}', request.FILES['picture'])
            try:
                picture_path = default_storage.path(name)
            except NotImplementedError:
                picture_path = None  # storage remoto não expõe path local

        # Garante que as contas são do usuário.
        owned_ids = list(
            InstagramAccount.objects.filter(id__in=account_ids, owner=request.user).values_list('id', flat=True)
        )
        bulk_edit_profiles.delay(owned_ids, full_name, biography, external_url, picture_path)
        messages.success(request, f'Edição de perfil disparada para {len(owned_ids)} conta(s). Pode levar alguns minutos.')
        return redirect('instagram:bulk_edit')

    accounts = InstagramAccount.objects.filter(owner=request.user)
    return render(request, 'instagram/bulk_edit.html', {'accounts': accounts})
