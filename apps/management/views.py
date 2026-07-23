from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.contrib import messages
from apps.instagram.models import InstagramAccount
from apps.publisher.models import ScheduledPost

User = get_user_model()

@staff_member_required
def dashboard(request):
    total_users = User.objects.count()
    pending_users = User.objects.filter(is_active=False).count()
    total_ig = InstagramAccount.objects.count()
    total_posts = ScheduledPost.objects.count()
    recent_posts = ScheduledPost.objects.select_related('account', 'account__owner').order_by('-created_at')[:5]
    
    context = {
        'total_users': total_users,
        'pending_users': pending_users,
        'total_ig': total_ig,
        'total_posts': total_posts,
        'recent_posts': recent_posts,
    }
    return render(request, 'management/dashboard.html', context)

@staff_member_required
def users_list(request):
    users = User.objects.all().order_by('-date_joined')
    return render(request, 'management/users.html', {'users': users})

@staff_member_required
def user_toggle_active(request, user_id):
    if request.method == 'POST':
        user = get_object_or_404(User, id=user_id)
        if user == request.user:
            messages.error(request, 'Você não pode desativar sua própria conta.')
        else:
            user.is_active = not user.is_active
            user.save()
            status = 'ativada' if user.is_active else 'desativada'
            messages.success(request, f'Conta de {user.username} foi {status}.')
    return redirect('management:users')

@staff_member_required
def user_toggle_ip_lock(request, user_id):
    """Liga/desliga a trava de IP e avisa o usuário por notificação."""
    if request.method != 'POST':
        return redirect('management:users')

    from apps.notifications.models import Notification
    user = get_object_or_404(User, id=user_id)
    user.ip_locked = not user.ip_locked

    if user.ip_locked:
        # Fixa no último IP conhecido; se não houver, fixa no próximo login.
        if not user.bound_ip and user.last_login_ip:
            user.bound_ip = user.last_login_ip
        alvo = user.bound_ip or 'o próximo acesso'
        Notification.objects.create(
            user=user,
            title='Acesso travado por segurança',
            message=(f'O administrador vinculou sua conta a um único IP ({alvo}). '
                     'Você só conseguirá entrar a partir desse local. '
                     'Isso evita o compartilhamento da sua conta.'),
            notification_type='warning',
        )
        messages.success(request, f'Trava de IP ATIVADA para {user.username} ({user.bound_ip or "no próximo login"}).')
    else:
        user.bound_ip = ''
        Notification.objects.create(
            user=user,
            title='Trava de acesso removida',
            message='O administrador liberou o acesso da sua conta de qualquer IP.',
            notification_type='info',
        )
        messages.success(request, f'Trava de IP desativada para {user.username}.')

    user.save(update_fields=['ip_locked', 'bound_ip'])
    return redirect('management:users')


@staff_member_required
def user_reset_ip(request, user_id):
    """Esquece o IP fixado (rebind no próximo login)."""
    if request.method == 'POST':
        user = get_object_or_404(User, id=user_id)
        user.bound_ip = ''
        user.save(update_fields=['bound_ip'])
        messages.success(request, f'IP de {user.username} liberado. Será fixado no próximo acesso.')
    return redirect('management:users')


@staff_member_required
def user_delete(request, user_id):
    """Exclui um usuário (e tudo dele, via cascade)."""
    if request.method != 'POST':
        return redirect('management:users')
    user = get_object_or_404(User, id=user_id)
    if user == request.user:
        messages.error(request, 'Você não pode excluir a própria conta.')
    elif user.is_superuser:
        messages.error(request, 'Não é possível excluir um administrador por aqui.')
    else:
        nome = user.username
        user.delete()
        messages.success(request, f'Usuário {nome} excluído.')
    return redirect('management:users')


@staff_member_required
def users_purge_unapproved(request):
    """Remove todos os cadastros ainda não aprovados (is_active=False)."""
    if request.method == 'POST':
        qs = User.objects.filter(is_active=False, is_superuser=False).exclude(id=request.user.id)
        n = qs.count()
        qs.delete()
        messages.success(request, f'{n} cadastro(s) não aprovado(s) removido(s).')
    return redirect('management:users')


@staff_member_required
def instagram_list(request):
    accounts = InstagramAccount.objects.select_related('owner').all().order_by('-created_at')
    return render(request, 'management/instagram.html', {'accounts': accounts})


# =============================================================================
# Moderação — o admin inspeciona o que está sendo postado (silencioso: o
# usuário não é avisado) e pode banir contas.
# =============================================================================
@staff_member_required
def moderation(request):
    """Lista todos os usuários com suas contas, para revisão manual."""
    from django.db.models import Count, Q

    q = (request.GET.get('q') or '').strip()
    usuarios = (User.objects.all()
                .annotate(
                    n_contas=Count('instagramaccount', distinct=True),
                    n_posts=Count('scheduledpost', distinct=True),
                )
                .order_by('-n_posts'))
    if q:
        usuarios = usuarios.filter(Q(username__icontains=q) | Q(nickname__icontains=q))

    # Contas de cada usuário, já carregadas para a tela.
    contas = (InstagramAccount.objects.select_related('owner')
              .order_by('owner_id', 'ig_username'))
    por_usuario = {}
    for c in contas:
        por_usuario.setdefault(c.owner_id, []).append(c)

    linhas = [{'user': u, 'contas': por_usuario.get(u.id, [])} for u in usuarios]
    return render(request, 'management/moderation.html', {'linhas': linhas, 'q': q})


@staff_member_required
def moderation_account(request, account_id):
    """Mostra o conteúdo que uma conta está postando, para avaliação manual."""
    from django.core.paginator import Paginator

    account = get_object_or_404(InstagramAccount.objects.select_related('owner'), id=account_id)
    posts = (ScheduledPost.objects.filter(account=account)
             .exclude(video_file='')
             .order_by('-scheduled_for'))
    paginator = Paginator(posts, 24)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'management/moderation_account.html', {
        'account': account,
        'posts': page,
        'page_obj': page,
        'total': paginator.count,
    })


@staff_member_required
def account_ban(request, account_id):
    """Bane/desbane uma conta (silencioso — o usuário não é notificado)."""
    if request.method != 'POST':
        return redirect('management:moderation')
    from django.utils import timezone

    account = get_object_or_404(InstagramAccount, id=account_id)
    account.banned_by_admin = not account.banned_by_admin
    if account.banned_by_admin:
        account.banned_reason = (request.POST.get('reason') or '').strip()[:255]
        account.banned_at = timezone.now()
        messages.success(request, f'Conta @{account.ig_username} banida. Não publica mais.')
    else:
        account.banned_reason = ''
        account.banned_at = None
        messages.success(request, f'Conta @{account.ig_username} desbanida. Voltou a publicar.')
    account.save(update_fields=['banned_by_admin', 'banned_reason', 'banned_at'])
    return redirect(request.POST.get('next') or 'management:moderation')

@staff_member_required
def posts_list(request):
    posts = ScheduledPost.objects.select_related('account', 'account__owner').all().order_by('-created_at')
    return render(request, 'management/posts.html', {'posts': posts})
