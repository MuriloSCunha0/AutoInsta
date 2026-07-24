import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_POST

from .alertas import preferencias
from .models import Notification, PushSubscription

@login_required
def list_notifications(request):
    notifications = Notification.objects.filter(user=request.user).order_by('-created_at')
    # Marca as não lidas como lidas ao abrir a central.
    unread = notifications.filter(is_read=False)
    if unread.exists():
        unread.update(is_read=True)
    return render(request, 'notifications/list.html', {'notifications': notifications})


@login_required
def alert_settings(request):
    """Tela de configuração dos alertas."""
    return render(request, 'notifications/alertas.html',
                  {'pref': preferencias(request.user)})


@login_required
@require_POST
def alert_settings_save(request):
    pref = preferencias(request.user)

    for campo in ('conta_caiu', 'falha_publicacao', 'limite_atingido',
                  'meta_views', 'resumo_diario'):
        setattr(pref, campo, request.POST.get(campo) == 'on')

    try:
        pref.meta_views_alvo = max(int(request.POST.get('meta_views_alvo') or 0), 0)
    except (TypeError, ValueError):
        pref.meta_views_alvo = 10000

    pref.telegram_chat_id = (request.POST.get('telegram_chat_id') or '').strip()[:64]
    # Só troca o token se o usuário digitou um novo (o campo vem vazio quando
    # ele não quis mexer — senão salvar a tela apagaria o token guardado).
    token_novo = (request.POST.get('telegram_token') or '').strip()
    if token_novo:
        pref.set_telegram_token(token_novo)
    elif request.POST.get('limpar_telegram') == 'on':
        pref.set_telegram_token('')

    pref.save()
    messages.success(request, 'Preferências de alerta salvas.')
    return redirect('notifications:alertas')


@login_required
@require_POST
def alert_test(request):
    """Manda um alerta de teste — prova que o celular está recebendo."""
    from .alertas import _enviar_telegram

    pref = preferencias(request.user)
    Notification.objects.create(
        user=request.user, title='Alerta de teste',
        message='Se você está lendo isto, os alertas do painel funcionam.',
        notification_type='success',
    )
    if pref.telegram_ativo:
        if _enviar_telegram(pref, 'Alerta de teste',
                            'Tudo certo! Os alertas do SandraoFlow chegam aqui.'):
            messages.success(request, 'Teste enviado — confira o Telegram no celular.')
        else:
            messages.error(
                request,
                'O Telegram recusou o envio. Confira o chat ID e se você já '
                'enviou /start para o bot.')
    # Web Push (PWA): notifica os aparelhos inscritos.
    from .push import enviar_push
    n = enviar_push(request.user, 'Notificação de teste',
                    'Se apareceu no seu celular, está tudo certo! 🎉',
                    url='/notifications/')
    if n:
        messages.success(request, f'Teste enviado para {n} aparelho(s).')
    elif pref.telegram_ativo:
        _enviar_telegram(pref, 'Alerta de teste', 'Tudo certo!')
        messages.success(request, 'Teste enviado ao Telegram.')
    else:
        messages.info(request, 'Teste criado no sino. Ative as notificações neste aparelho para receber no celular.')
    return redirect('notifications:alertas')


# =============================================================================
# PWA + Web Push
# =============================================================================
@login_required
def push_public_key(request):
    """Chave pública VAPID que o navegador usa para se inscrever."""
    return JsonResponse({'publicKey': getattr(settings, 'VAPID_PUBLIC_KEY', '')})


@login_required
@require_POST
def push_subscribe(request):
    """Registra (ou atualiza) a inscrição de push deste aparelho."""
    try:
        dados = json.loads(request.body.decode('utf-8'))
        endpoint = dados['endpoint']
        keys = dados['keys']
    except (ValueError, KeyError):
        return JsonResponse({'ok': False, 'erro': 'dados inválidos'}, status=400)

    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            'user': request.user,
            'p256dh': keys.get('p256dh', ''),
            'auth': keys.get('auth', ''),
            'user_agent': request.META.get('HTTP_USER_AGENT', '')[:255],
        },
    )
    return JsonResponse({'ok': True})


@login_required
@require_POST
def push_unsubscribe(request):
    try:
        endpoint = json.loads(request.body.decode('utf-8')).get('endpoint', '')
    except ValueError:
        endpoint = ''
    if endpoint:
        PushSubscription.objects.filter(endpoint=endpoint, user=request.user).delete()
    return JsonResponse({'ok': True})


@cache_control(max_age=3600)
def manifest(request):
    """manifest.webmanifest — faz o site ser instalável como app."""
    dados = {
        'name': 'SandraoFlow',
        'short_name': 'SandraoFlow',
        'description': 'Automação de publicações no Instagram',
        'start_url': '/',
        'scope': '/',
        'display': 'standalone',
        'background_color': '#0a0e1a',
        'theme_color': '#0a0e1a',
        'lang': 'pt-BR',
        'icons': [
            {'src': '/icon-192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any maskable'},
            {'src': '/icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any maskable'},
        ],
    }
    return JsonResponse(dados, content_type='application/manifest+json')


@cache_control(max_age=600)
def service_worker(request):
    """sw.js servido da raiz para controlar todo o site (escopo /)."""
    from django.template.loader import render_to_string
    js = render_to_string('pwa/sw.js')
    resp = HttpResponse(js, content_type='application/javascript')
    resp['Service-Worker-Allowed'] = '/'
    return resp


@cache_control(max_age=86400)
def app_icon(request, size):
    """Ícone do app (estrela de Davi) gerado na hora com Pillow."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    size = 512 if int(size) >= 512 else 192
    img = Image.new('RGB', (size, size), '#0a0e1a')
    d = ImageDraw.Draw(img)
    cx = cy = size / 2
    r = size * 0.34

    import math

    def triangulo(offset):
        pts = []
        for ang in (90, 210, 330):
            a = math.radians(ang + offset)
            pts.append((cx + r * math.cos(a), cy - r * math.sin(a)))
        return pts

    largura = max(2, int(size * 0.03))
    d.polygon(triangulo(0), outline='#8b5cf6', width=largura)
    d.polygon(triangulo(180), outline='#8b5cf6', width=largura)

    buf = BytesIO()
    img.save(buf, format='PNG')
    return HttpResponse(buf.getvalue(), content_type='image/png')
