from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from apps.instagram.models import InstagramAccount
from apps.publisher.models import ScheduledPost
from django.db.models import Sum

@login_required
def dashboard(request):
    accounts = InstagramAccount.objects.filter(owner=request.user)
    posts_queued = ScheduledPost.objects.filter(owner=request.user, status='queued').count()
    
    followers_total = accounts.aggregate(Sum('followers_count'))['followers_count__sum'] or 0
    
    recent_posts = ScheduledPost.objects.filter(owner=request.user).order_by('-created_at')[:5]
    
    context = {
        'accounts_count': accounts.count(),
        'queued_count': posts_queued,
        'followers_total': followers_total,
        'recent_posts': recent_posts,
        'accounts': accounts
    }
    return render(request, 'dashboard/index.html', context)
