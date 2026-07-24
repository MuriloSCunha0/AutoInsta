"""Vigia as contas e dispara os alertas configurados pelo usuário."""
from datetime import timedelta

from celery import shared_task
from django.db.models import Sum
from django.utils import timezone

from apps.instagram.models import InstagramAccount
from apps.publisher.models import ScheduledPost

from .alertas import alertar

# Status que significam "a conta caiu" (perdeu permissão / não publica mais).
STATUS_CAIU = ('error', 'banned', 'session_expired')


@shared_task
def checar_alertas():
    """Roda periodicamente e avisa o que o usuário pediu para ser avisado."""
    agora = timezone.now()
    hoje = timezone.localdate()
    janela_24h = agora - timedelta(hours=24)

    for conta in InstagramAccount.objects.select_related('owner'):
        dono = conta.owner

        # ── Conta caiu (o alerta da referência) ───────────────────────
        if conta.status in STATUS_CAIU:
            alertar(
                dono, 'conta_caiu',
                'Conta desconectada',
                f'@{conta.ig_username} perdeu permissão na Meta. '
                f'{(conta.last_error or "").strip()[:120]}'.strip(),
                chave=f'caiu:{conta.id}:{conta.status}',
                nivel='error', account=conta,
            )

        if conta.banned_by_admin:
            alertar(
                dono, 'conta_caiu',
                'Conta bloqueada',
                f'@{conta.ig_username} está bloqueada e não vai publicar.',
                chave=f'banida:{conta.id}', nivel='error', account=conta,
            )

        # ── Limite atingido / cooldown ───────────────────────────────
        if not conta.ignorar_limites:
            if conta.em_cooldown:
                alertar(
                    dono, 'limite_atingido',
                    'Conta limitada pela Meta',
                    f'@{conta.ig_username} atingiu o limite da Meta e está em espera. '
                    'A fila continua quando liberar — ou ligue "Forçar postagem".',
                    chave=f'cooldown:{conta.id}', account=conta,
                )
            else:
                limite = conta.daily_post_limit or 0
                if limite > 0:
                    publicados = ScheduledPost.objects.filter(
                        account=conta, status='published',
                        published_at__gte=janela_24h).count()
                    if publicados >= limite:
                        alertar(
                            dono, 'limite_atingido',
                            'Limite diário atingido',
                            f'@{conta.ig_username} bateu o teto de {limite} '
                            f'publicações em 24h.',
                            chave=f'teto:{conta.id}:{hoje}', account=conta,
                        )

    # ── Meta de views do dia (por usuário) ───────────────────────────
    from apps.accounts.models import User

    for dono in User.objects.filter(is_active=True):
        total = (InstagramAccount.objects.filter(owner=dono)
                 .aggregate(v=Sum('views_today'))['v'] or 0)
        if not total:
            continue
        from .alertas import preferencias
        pref = preferencias(dono)
        alvo = pref.meta_views_alvo or 0
        if alvo > 0 and total >= alvo:
            alertar(
                dono, 'meta_views',
                'Meta de views batida! 🎉',
                f'Você já tem {total:,} visualizações hoje '
                f'(meta: {alvo:,}).'.replace(',', '.'),
                chave=f'metaviews:{dono.id}:{hoje}', nivel='success',
            )


@shared_task
def resumo_diario():
    """Fecha o dia com o resumo de quem pediu."""
    from apps.accounts.models import User

    hoje = timezone.localdate()
    for dono in User.objects.filter(is_active=True):
        publicados = ScheduledPost.objects.filter(
            owner=dono, status='published', published_at__date=hoje).count()
        if not publicados:
            continue
        contas = (ScheduledPost.objects
                  .filter(owner=dono, status='published', published_at__date=hoje)
                  .values('account').distinct().count())
        views = (InstagramAccount.objects.filter(owner=dono)
                 .aggregate(v=Sum('views_today'))['v'] or 0)
        alertar(
            dono, 'resumo_diario',
            'Resumo do dia',
            f'{publicados} publicações em {contas} conta(s). '
            f'{views:,} visualizações hoje.'.replace(',', '.'),
            chave=f'resumo:{dono.id}:{hoje}', nivel='info',
        )
