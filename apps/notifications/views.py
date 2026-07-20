from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import Notification

@login_required
def list_notifications(request):
    notifications = Notification.objects.filter(user=request.user).order_by('-created_at')
    # Marca as não lidas como lidas ao abrir a central.
    unread = notifications.filter(is_read=False)
    if unread.exists():
        unread.update(is_read=True)
    return render(request, 'notifications/list.html', {'notifications': notifications})
