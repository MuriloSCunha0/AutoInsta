from .models import Notification


def notifications(request):
    """Injeta as notificações do usuário no sino da topbar (todas as páginas)."""
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {}
    qs = Notification.objects.filter(user=request.user).order_by('-created_at')
    return {
        'nav_notifications': qs[:6],
        'nav_unread_count': qs.filter(is_read=False).count(),
    }
