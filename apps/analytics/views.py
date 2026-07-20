from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from apps.instagram.models import InstagramAccount
from apps.publisher.models import ScheduledPost
from django.db.models import Sum, Count, Q, F
from django.db.models.functions import Coalesce
from django.contrib.auth import get_user_model

User = get_user_model()

@login_required
def dashboard(request):
    accounts = InstagramAccount.objects.filter(owner=request.user)
    posts_queued = ScheduledPost.objects.filter(owner=request.user, status='queued').count()
    
    followers_total = accounts.aggregate(Sum('followers_count'))['followers_count__sum'] or 0
    
    recent_posts = ScheduledPost.objects.filter(owner=request.user).order_by('-created_at')[:5]
    
    # Calculate Global Ranking
    # 1 follower = 1 point, 1 published post = 50 points
    users_with_metrics = User.objects.filter(is_active=True).annotate(
        total_followers=Coalesce(Sum('instagramaccount__followers_count'), 0),
        successful_posts=Count('scheduledpost', filter=Q(scheduledpost__status='published'))
    ).annotate(
        ranking_score=F('total_followers') + (F('successful_posts') * 50)
    ).order_by('-ranking_score')[:5]

    ranking_list = []
    for idx, u in enumerate(users_with_metrics):
        display_name = u.username
        if len(display_name) > 3:
            display_name = display_name[:3] + '***'
        else:
            display_name = display_name + '***'
            
        ranking_list.append({
            'position': idx + 1,
            'username': display_name,
            'score': u.ranking_score,
            'followers': u.total_followers,
            'posts': u.successful_posts,
            'is_me': u.id == request.user.id
        })
    
    context = {
        'accounts_count': accounts.count(),
        'queued_count': posts_queued,
        'followers_total': followers_total,
        'recent_posts': recent_posts,
        'accounts': accounts,
        'ranking_list': ranking_list,
    }
    return render(request, 'dashboard/index.html', context)
