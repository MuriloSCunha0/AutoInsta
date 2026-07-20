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
def instagram_list(request):
    accounts = InstagramAccount.objects.select_related('owner').all().order_by('-created_at')
    return render(request, 'management/instagram.html', {'accounts': accounts})

@staff_member_required
def posts_list(request):
    posts = ScheduledPost.objects.select_related('account', 'account__owner').all().order_by('-created_at')
    return render(request, 'management/posts.html', {'posts': posts})
