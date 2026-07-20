import json
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
from .models import InstagramAccount
import requests
from urllib.parse import urlencode
from .forms import AddInstagramAccountForm
from django.core.cache import cache

from .tasks import (
    connect_by_sessionid,
    web_login_account, claim_login_generation,
)


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
    Salva uma conta usando o Token da Meta Graph API.
    """
    ig_username = request.POST.get('ig_username', '').strip().lower()
    meta_access_token = request.POST.get('meta_access_token', '').strip()
    ig_user_id = request.POST.get('ig_user_id', '').strip()

    if not ig_username or not meta_access_token:
        return HttpResponse(
            '<div class="alert alert-danger"><i class="bi bi-exclamation-triangle"></i> Usuário e Token Meta são obrigatórios.</div>'
        )

    # Verifica limite de contas
    current_count = InstagramAccount.objects.filter(owner=request.user).count()
    if current_count >= request.user.max_ig_accounts:
        return HttpResponse(
            '<div class="alert alert-warning"><i class="bi bi-exclamation-triangle"></i> '
            f'Limite do plano atingido (máx {request.user.max_ig_accounts} contas). Assine o PRO.</div>'
        )

    try:
        acc, created = InstagramAccount.objects.get_or_create(
            owner=request.user,
            ig_username=ig_username,
            defaults={
                'meta_access_token': meta_access_token,
                'status': 'active', # Se já temos token meta, ela pode ser considerada ativa
                'ig_user_id': int(ig_user_id) if ig_user_id.isdigit() else None
            }
        )

        if not created:
            # Atualiza token existente
            acc.meta_access_token = meta_access_token
            if ig_user_id.isdigit():
                acc.ig_user_id = int(ig_user_id)
            acc.status = 'active'
            acc.save()

        # Retorna o card atualizado para inserir na lista via HTMX
        return render(request, 'instagram/partials/account_card.html', {'account': acc})

    except Exception as e:
        return HttpResponse(
            f'<div class="alert alert-danger"><i class="bi bi-exclamation-triangle"></i> Erro ao salvar Token Meta: {str(e)}</div>'
        )


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
    app_id = getattr(settings, 'META_APP_ID', '')
    redirect_uri = getattr(settings, 'META_REDIRECT_URI', '')
    
    if not app_id:
        return HttpResponse('<div class="alert alert-danger">META_APP_ID não configurado no .env.</div>')

    scopes = [
        'instagram_business_basic',
        'instagram_business_manage_messages',
        'instagram_business_manage_comments',
        'instagram_business_content_publish',
        'instagram_business_manage_insights',
    ]
    
    params = {
        'client_id': app_id,
        'redirect_uri': redirect_uri,
        'scope': ','.join(scopes),
        'response_type': 'code',
        'state': 'new',
    }
    
    auth_url = f"https://www.instagram.com/oauth/authorize?{urlencode(params)}"
    
    context = {
        'auth_url': auth_url
    }
    return render(request, 'instagram/partials/oauth_link.html', context)


@login_required
def oauth_callback(request):
    """Callback redirecionado pelo Facebook."""
    code = request.GET.get('code')
    error = request.GET.get('error')
    
    if error:
        messages.error(request, f"Erro na autorização Meta: {error}")
        return redirect('instagram:list')
        
    if not code:
        messages.error(request, "Nenhum código de autorização recebido.")
        return redirect('instagram:list')
        
    app_id = getattr(settings, 'META_APP_ID', '')
    app_secret = getattr(settings, 'META_APP_SECRET', '')
    redirect_uri = getattr(settings, 'META_REDIRECT_URI', '')
    
    # 1. Trocar código por short-lived token
    url = "https://api.instagram.com/oauth/access_token"
    payload = {
        'client_id': app_id,
        'client_secret': app_secret,
        'grant_type': 'authorization_code',
        'redirect_uri': redirect_uri,
        'code': code
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
            'access_token': short_token
        }
        
        ll_response = requests.get(ll_url, params=ll_params, timeout=15)
        ll_data = ll_response.json()
        
        access_token = ll_data.get('access_token', short_token)
            
        # 3. Buscar Perfil Instagram
        me_url = f"https://graph.instagram.com/v21.0/{user_id}" if user_id else "https://graph.instagram.com/v21.0/me"
        me_params = {
            'fields': 'id,username,name,followers_count,media_count,profile_picture_url',
            'access_token': access_token
        }
        
        profile_data = {}
        me_response = requests.get(me_url, params=me_params, timeout=15)
        if me_response.status_code == 200:
            profile_data = me_response.json()
            
        if not profile_data.get('username') and not user_id:
            me_response_fb = requests.get("https://graph.instagram.com/v21.0/me", params=me_params, timeout=15)
            if me_response_fb.status_code == 200:
                profile_data = me_response_fb.json()
                
        username = profile_data.get('username', user_id)
        if not username:
             messages.error(request, "Não foi possível obter o username da Meta.")
             return redirect('instagram:list')
        
        # 4. Salvar / Atualizar Conta
        account, created = InstagramAccount.objects.get_or_create(
            owner=request.user,
            ig_username=username,
            defaults={
                'meta_access_token': access_token,
                'ig_user_id': int(user_id) if user_id.isdigit() else None,
                'status': 'active'
            }
        )
        
        if not created:
            account.meta_access_token = access_token
            if user_id.isdigit():
                account.ig_user_id = int(user_id)
            account.status = 'active'
            if ig_user_id.isdigit():
                acc.ig_user_id = int(ig_user_id)
            acc.status = 'active'
            acc.save()

        # Retorna o card atualizado para inserir na lista via HTMX
        return render(request, 'instagram/partials/account_card.html', {'account': acc})

    except Exception as e:
        return HttpResponse(
            f'<div class="alert alert-danger"><i class="bi bi-exclamation-triangle"></i> Erro ao salvar Token Meta: {str(e)}</div>'
        )


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
    app_id = getattr(settings, 'META_APP_ID', '')
    redirect_uri = getattr(settings, 'META_REDIRECT_URI', '')
    
    if not app_id:
        return HttpResponse('<div class="alert alert-danger">META_APP_ID não configurado no .env.</div>')

    scopes = [
        'instagram_business_basic',
        'instagram_business_manage_messages',
        'instagram_business_manage_comments',
        'instagram_business_content_publish',
        'instagram_business_manage_insights',
    ]
    
    params = {
        'client_id': app_id,
        'redirect_uri': redirect_uri,
        'scope': ','.join(scopes),
        'response_type': 'code',
        'state': 'new',
    }
    
    auth_url = f"https://www.instagram.com/oauth/authorize?{urlencode(params)}"
    
    context = {
        'auth_url': auth_url
    }
    return render(request, 'instagram/partials/oauth_link.html', context)


@login_required
def oauth_callback(request):
    """Callback redirecionado pelo Facebook."""
    code = request.GET.get('code')
    error = request.GET.get('error')
    
    if error:
        messages.error(request, f"Erro na autorização Meta: {error}")
        return redirect('instagram:list')
        
    if not code:
        messages.error(request, "Nenhum código de autorização recebido.")
        return redirect('instagram:list')
        
    app_id = getattr(settings, 'META_APP_ID', '')
    app_secret = getattr(settings, 'META_APP_SECRET', '')
    redirect_uri = getattr(settings, 'META_REDIRECT_URI', '')
    
    # 1. Trocar código por short-lived token
    url = "https://api.instagram.com/oauth/access_token"
    payload = {
        'client_id': app_id,
        'client_secret': app_secret,
        'grant_type': 'authorization_code',
        'redirect_uri': redirect_uri,
        'code': code
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
            'access_token': short_token
        }
        
        ll_response = requests.get(ll_url, params=ll_params, timeout=15)
        ll_data = ll_response.json()
        
        access_token = ll_data.get('access_token', short_token)
            
        # 3. Buscar Perfil Instagram
        me_url = f"https://graph.instagram.com/v21.0/{user_id}" if user_id else "https://graph.instagram.com/v21.0/me"
        me_params = {
            'fields': 'id,username,name,followers_count,media_count,profile_picture_url',
            'access_token': access_token
        }
        
        profile_data = {}
        me_response = requests.get(me_url, params=me_params, timeout=15)
        if me_response.status_code == 200:
            profile_data = me_response.json()
            
        if not profile_data.get('username') and not user_id:
            me_response_fb = requests.get("https://graph.instagram.com/v21.0/me", params=me_params, timeout=15)
            if me_response_fb.status_code == 200:
                profile_data = me_response_fb.json()
                
        username = profile_data.get('username', user_id)
        if not username:
             messages.error(request, "Não foi possível obter o username da Meta.")
             return redirect('instagram:list')
        
        # 4. Salvar / Atualizar Conta
        account, created = InstagramAccount.objects.get_or_create(
            owner=request.user,
            ig_username=username,
            defaults={
                'meta_access_token': access_token,
                'ig_user_id': int(user_id) if user_id.isdigit() else None,
                'status': 'active'
            }
        )
        
        if not created:
            account.meta_access_token = access_token
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
    return render(request, 'instagram/profile.html')

@login_required
def proxies(request):
    return render(request, 'instagram/proxies.html')
