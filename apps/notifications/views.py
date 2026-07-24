from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .alertas import preferencias
from .models import Notification

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
    else:
        messages.info(request, 'Teste criado no sino. Configure o Telegram para receber no celular.')
    return redirect('notifications:alertas')
