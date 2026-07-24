"""Envio de alertas: sino do painel + Telegram (celular).

Regra de ouro: NÃO repetir o mesmo alerta. Uma conta que caiu continua caída
por horas; sem trava, o usuário receberia o mesmo aviso a cada rodada e
acabaria silenciando tudo — que é o oposto do objetivo.
"""
import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

# Quanto tempo o mesmo alerta fica silenciado depois de enviado.
JANELA_ANTI_REPETICAO = 6 * 60 * 60  # 6 horas


def preferencias(user):
    """Preferências do usuário (cria com os padrões na primeira vez)."""
    from .models import AlertPreference
    pref, _ = AlertPreference.objects.get_or_create(user=user)
    return pref


def _ja_avisou(chave):
    """True se este alerta já saiu há pouco (e marca o envio)."""
    if cache.get(chave):
        return True
    cache.set(chave, 1, JANELA_ANTI_REPETICAO)
    return False


def _enviar_telegram(pref, titulo, mensagem):
    import requests

    token = pref.get_telegram_token()
    if not (token and pref.telegram_chat_id):
        return False
    try:
        resp = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            data={'chat_id': pref.telegram_chat_id,
                  'text': f'*{titulo}*\n{mensagem}',
                  'parse_mode': 'Markdown'},
            timeout=12,
        )
        return resp.status_code == 200
    except Exception as erro:
        logger.warning('Telegram falhou para %s: %s', pref.user_id, erro)
        return False


def alertar(user, tipo_pref, titulo, mensagem, chave, nivel='warning', account=None):
    """Dispara um alerta se o usuário quiser recebê-lo e ainda não recebeu.

    `tipo_pref` é o nome do campo em AlertPreference (ex.: 'conta_caiu').
    `chave` identifica o alerta para não repetir (ex.: 'caiu:12').
    """
    from .models import Notification

    pref = preferencias(user)
    if not getattr(pref, tipo_pref, False):
        return False
    if _ja_avisou(f'alerta:{user.id}:{chave}'):
        return False

    Notification.objects.create(
        user=user, title=titulo, message=mensagem,
        notification_type=nivel, related_account=account,
    )
    # Notificação no celular pelo próprio site (Web Push / PWA).
    try:
        from .push import enviar_push
        enviar_push(user, titulo, mensagem, url='/notifications/')
    except Exception:
        pass
    # Telegram continua disponível como canal extra opcional.
    _enviar_telegram(pref, titulo, mensagem)
    return True
