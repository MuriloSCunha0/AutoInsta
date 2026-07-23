from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from apps.instagram.models import InstagramAccount
from apps.publisher.models import ScheduledPost
from django.db.models import Sum, Count, Q, F
from django.db.models.functions import Coalesce
from django.contrib.auth import get_user_model

User = get_user_model()

@login_required
def dashboard(request):
    from django.utils import timezone
    from datetime import timedelta
    accounts = InstagramAccount.objects.filter(owner=request.user)
    posts_queued = ScheduledPost.objects.filter(owner=request.user, status='queued').count()

    # Números reais da Meta, somados nas contas do usuário.
    somas = accounts.aggregate(
        seguidores=Sum('followers_count'),
        views=Sum('views_total'),
        views_hoje=Sum('views_today'),
    )
    followers_total = somas['seguidores'] or 0
    views_total = somas['views'] or 0
    views_today = somas['views_hoje'] or 0

    today = timezone.localdate()
    published_today = ScheduledPost.objects.filter(
        owner=request.user, status='published', published_at__date=today
    ).count()
    published_yesterday = ScheduledPost.objects.filter(
        owner=request.user, status='published',
        published_at__date=today - timedelta(days=1)
    ).count()

    # O que ainda vai sair — publicado já saiu da fila e vive no histórico.
    # Antes esta lista era "os 5 últimos criados", que com o volume diário
    # virava só publicados e não mostrava nada do que estava por vir.
    recent_posts = (ScheduledPost.objects
                    .filter(owner=request.user, status__in=ScheduledPost.STATUS_ATIVOS)
                    .select_related('account')
                    .order_by('scheduled_for')[:5])

    # Ranking DO DIA: quem mais publicou hoje (posts publicados com published_at
    # na data de hoje). Empate desempata por seguidores.
    users_with_metrics = (
        User.objects.filter(is_active=True)
        .annotate(
            posts_hoje=Count(
                'scheduledpost',
                filter=Q(scheduledpost__status='published',
                         scheduledpost__published_at__date=today),
            ),
            total_followers=Coalesce(Sum('instagramaccount__followers_count'), 0),
            total_views=Coalesce(Sum('instagramaccount__views_total'), 0),
            views_hoje=Coalesce(Sum('instagramaccount__views_today'), 0),
        )
        .filter(posts_hoje__gt=0)
        .order_by('-posts_hoje', '-total_followers')[:10]
    )

    ranking_list = []
    for idx, u in enumerate(users_with_metrics):
        ranking_list.append({
            'position': idx + 1,
            'username': u.username,  # sem censura, a pedido
            'posts': u.posts_hoje,
            'followers': u.total_followers,
            'views': u.total_views,
            'views_hoje': u.views_hoje,
            'is_me': u.id == request.user.id,
        })
    
    context = {
        'accounts_count': accounts.count(),
        'queued_count': posts_queued,
        'followers_total': followers_total,
        'views_total': views_total,
        'views_today': views_today,
        'published_today': published_today,
        'published_yesterday': published_yesterday,
        'recent_posts': recent_posts,
        'accounts': accounts,
        'ranking_list': ranking_list,
    }
    return render(request, 'dashboard/index.html', context)

@login_required
def performance(request):
    accounts = InstagramAccount.objects.filter(owner=request.user)
    followers_total = accounts.aggregate(Sum('followers_count'))['followers_count__sum'] or 0
    following_total = accounts.aggregate(Sum('following_count'))['following_count__sum'] or 0
    posts_total = accounts.aggregate(Sum('posts_count'))['posts_count__sum'] or 0
    
    # Calculate a mock engagement rate based on followers vs posts (since we'd need full insights API for true reach)
    engagement_rate = 0
    if followers_total > 0:
        # A simple dummy formula: assume each post gets 10% followers reach
        engagement_rate = round((posts_total * 0.1), 2)
        if engagement_rate > 100: engagement_rate = 100
        
    # Dados para os gráficos (Chart.js)
    import json
    chart_accounts = list(accounts.order_by('-followers_count')[:8].values_list('ig_username', flat=True))
    chart_followers = list(accounts.order_by('-followers_count')[:8].values_list('followers_count', flat=True))

    status_counts = ScheduledPost.objects.filter(owner=request.user).aggregate(
        published=Count('id', filter=Q(status='published')),
        queued=Count('id', filter=Q(status='queued')),
        processing=Count('id', filter=Q(status='processing')),
        failed=Count('id', filter=Q(status='failed')),
    )

    context = {
        'followers_total': followers_total,
        'following_total': following_total,
        'posts_total': posts_total,
        'engagement_rate': engagement_rate,
        'accounts_count': accounts.count(),
        'chart_accounts': json.dumps(chart_accounts),
        'chart_followers': json.dumps(chart_followers),
        'chart_status': json.dumps([
            status_counts['published'] or 0,
            status_counts['queued'] or 0,
            status_counts['processing'] or 0,
            status_counts['failed'] or 0,
        ]),
    }
    return render(request, 'analytics/performance.html', context)

import requests
from django.core.cache import cache

@login_required
def top_posts(request):
    accounts = InstagramAccount.objects.filter(owner=request.user, status='active').exclude(meta_access_token='')
    
    all_media = []
    total_views = 0
    total_likes = 0
    total_comments = 0
    
    for account in accounts:
        cache_key = f'ig_media_{account.id}'
        media_data = cache.get(cache_key)
        
        if not media_data:
            # Fetch from API
            ig_user_id = account.ig_user_id or 'me'
            url = f"https://graph.instagram.com/v23.0/{ig_user_id}/media"
            params = {
                'fields': 'id,caption,media_type,media_url,permalink,thumbnail_url,timestamp,username,comments_count,like_count',
                'access_token': account.get_meta_token(),
                'limit': 20
            }
            try:
                response = requests.get(url, params=params, timeout=10)
                if response.status_code == 200:
                    media_data = response.json().get('data', [])
                    cache.set(cache_key, media_data, timeout=3600) # cache for 1 hour
                else:
                    media_data = []
            except Exception:
                media_data = []
                
        for m in media_data:
            # Add account info to the media object for display
            m['account_username'] = account.ig_username
            m['account_pic'] = account.profile_pic_url
            
            # Instagram sometimes returns views as part of insights, but for now we'll approximate 
            # if we can't get true views without the insights permission on the exact media.
            m['views'] = m.get('like_count', 0) * 8  # Dummy view multiplier for display
            
            all_media.append(m)
            
            total_views += m['views']
            total_likes += m.get('like_count', 0)
            total_comments += m.get('comments_count', 0)

    # Sort by engagement (likes + comments)
    all_media.sort(key=lambda x: x.get('like_count', 0) + x.get('comments_count', 0), reverse=True)
    
    top_media = all_media[:9] # Top 9 posts
    
    context = {
        'top_media': top_media,
        'total_views': total_views,
        'total_likes': total_likes,
        'total_comments': total_comments,
    }
    return render(request, 'analytics/top_posts.html', context)

@login_required
def health(request):
    accounts = InstagramAccount.objects.filter(owner=request.user)
    account_health = []
    
    for acc in accounts:
        status = 'ok'
        msg = 'Token Meta ativo e válido.'
        if not acc.meta_access_token:
            status = 'error'
            msg = 'Não conectado à API Meta.'
        else:
            # Check token validy
            url = "https://graph.instagram.com/v23.0/me"
            params = {'access_token': acc.get_meta_token()}
            try:
                res = requests.get(url, params=params, timeout=5)
                if res.status_code != 200:
                    status = 'error'
                    msg = 'Token Meta expirado ou inválido.'
            except Exception:
                status = 'warning'
                msg = 'Falha ao verificar com a Meta.'
                
        account_health.append({
            'username': acc.ig_username,
            'status': status,
            'message': msg
        })
        
    return render(request, 'analytics/health.html', {'account_health': account_health})

from .models import DailySnapshot, SystemLog

@login_required
def logs_view(request):
    logs = SystemLog.objects.filter(owner=request.user)
    return render(request, 'analytics/logs.html', {'logs': logs})


@login_required
def sync_top_posts(request):
    """Invalida o cache de mídias das contas para forçar nova busca na Meta API."""
    from django.contrib import messages
    accounts = InstagramAccount.objects.filter(owner=request.user)
    for acc in accounts:
        cache.delete(f'ig_media_{acc.id}')
    messages.success(request, 'Insights re-sincronizados a partir da API do Instagram.')
    return redirect('analytics:top_posts')
