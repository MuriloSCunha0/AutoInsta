"""Entrega de Web Push (PWA) — a notificação chega no celular pelo navegador.

Sem bot: o próprio site, instalado como app, recebe as notificações. Usa o
protocolo Web Push com chaves VAPID.
"""
import json
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def push_configurado():
    return bool(getattr(settings, 'VAPID_PRIVATE_KEY', '')
                and getattr(settings, 'VAPID_PUBLIC_KEY', ''))


def enviar_push(user, titulo, corpo, url='/'):
    """Manda a notificação para todos os aparelhos inscritos do usuário.

    Best-effort: uma inscrição morta (410/404) é removida e não derruba as
    outras. Retorna quantos aparelhos receberam.
    """
    if not push_configurado():
        return 0

    try:
        from pywebpush import WebPushException, webpush
    except Exception:
        logger.warning('pywebpush não instalado — Web Push desativado.')
        return 0

    from .models import PushSubscription

    payload = json.dumps({'title': titulo, 'body': corpo, 'url': url})
    vapid_claims = {'sub': f"mailto:{getattr(settings, 'VAPID_ADMIN_EMAIL', 'admin@sandraoflow.com')}"}
    enviados = 0

    for sub in PushSubscription.objects.filter(user=user):
        try:
            webpush(
                subscription_info=sub.como_dict(),
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims=dict(vapid_claims),
                timeout=10,
            )
            enviados += 1
        except WebPushException as e:
            # 410 Gone / 404: o navegador cancelou a inscrição — limpa.
            status = getattr(getattr(e, 'response', None), 'status_code', None)
            if status in (404, 410):
                sub.delete()
            else:
                logger.warning('Web Push falhou (user=%s): %s', user.id, e)
        except Exception as e:
            logger.warning('Web Push erro inesperado (user=%s): %s', user.id, e)

    return enviados
