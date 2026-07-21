from datetime import timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.files.storage import default_storage
from django.utils.dateparse import parse_datetime
from .models import ScheduledPost, PostLoop
from .forms import ScheduledPostForm, PostLoopForm
from apps.instagram.models import InstagramAccount
from apps.library.models import MediaAsset, CaptionSet
from django.http import JsonResponse
from django.utils import timezone

@login_required
def queue_list(request):
    posts = ScheduledPost.objects.filter(owner=request.user)
    form = ScheduledPostForm()
    # Limitar as contas no form apenas as do usuario
    form.fields['account'].queryset = form.fields['account'].queryset.filter(owner=request.user)
    return render(request, 'publisher/queue.html', {'posts': posts, 'form': form})

@login_required
def add_post(request):
    if request.method == 'POST':
        form = ScheduledPostForm(request.POST, request.FILES)
        if form.is_valid():
            post = form.save(commit=False)
            post.owner = request.user
            post.save()
            return redirect('publisher:queue')
    return redirect('publisher:queue')
    
@login_required
def remove_post(request, post_id):
    post = get_object_or_404(ScheduledPost, id=post_id, owner=request.user)
    post.delete()
    return redirect('publisher:queue')

@login_required
def loops(request):
    loops_list = PostLoop.objects.filter(owner=request.user)
    form = PostLoopForm()
    form.fields['account'].queryset = form.fields['account'].queryset.filter(owner=request.user)
    return render(request, 'publisher/loops.html', {'loops': loops_list, 'form': form})

@login_required
def add_loop(request):
    if request.method == 'POST':
        form = PostLoopForm(request.POST, request.FILES)
        if form.is_valid():
            loop = form.save(commit=False)
            loop.owner = request.user
            loop.save()
    return redirect('publisher:loops')

@login_required
def toggle_loop(request, loop_id):
    loop = get_object_or_404(PostLoop, id=loop_id, owner=request.user)
    loop.is_active = not loop.is_active
    loop.save()
    return redirect('publisher:loops')

@login_required
def delete_loop(request, loop_id):
    loop = get_object_or_404(PostLoop, id=loop_id, owner=request.user)
    loop.delete()
    return redirect('publisher:loops')

@login_required
def stories(request):
    posts = ScheduledPost.objects.filter(owner=request.user, post_type='STORY')
    form = ScheduledPostForm(initial={'post_type': 'STORY'})
    form.fields['account'].queryset = form.fields['account'].queryset.filter(owner=request.user)
    return render(request, 'publisher/stories.html', {'posts': posts, 'form': form})

@login_required
def composer(request):
    """Composer real: publica/agenda em massa (várias contas × várias mídias)."""
    user = request.user

    if request.method == 'POST':
        return _composer_submit(request)

    context = {
        'accounts': InstagramAccount.objects.filter(owner=user),
        'library_videos': MediaAsset.objects.filter(owner=user, kind='video'),
        'library_covers': MediaAsset.objects.filter(owner=user, kind='image'),
        'caption_sets': CaptionSet.objects.filter(owner=user),
        'post_types': ScheduledPost.TYPE_CHOICES,
    }
    return render(request, 'publisher/composer.html', context)


def _composer_submit(request):
    user = request.user

    account_ids = request.POST.getlist('accounts')
    library_video_ids = request.POST.getlist('library_videos')
    caption = (request.POST.get('caption') or '').strip()
    caption_set_id = request.POST.get('caption_set')
    post_type = request.POST.get('post_type', 'REELS')
    cover_library_id = request.POST.get('cover_library')

    # Reels + grade (share_to_feed) — valor 'grade' liga; 'reels' deixa só na aba.
    share_to_feed = request.POST.get('grade', 'grade') == 'grade'

    # Hashtags: anexadas ao final da legenda.
    hashtags = (request.POST.get('hashtags') or '').strip()
    if hashtags:
        caption = f"{caption}\n\n{hashtags}".strip()

    # Repetir cada mídia: sobe 1 vídeo, cria N posts (sem duplicar arquivo).
    try:
        repeat = max(int(request.POST.get('repeat', 1)), 1)
    except (TypeError, ValueError):
        repeat = 1

    # Parar em (opcional): não agenda posts com horário além dessa data.
    end_at = None
    if request.POST.get('end'):
        end_at = parse_datetime(request.POST.get('end'))
        if end_at and timezone.is_naive(end_at):
            end_at = timezone.make_aware(end_at, timezone.get_current_timezone())

    # ── Início do agendamento ──────────────────────────────────
    mode = request.POST.get('schedule_mode', 'now')
    if mode == 'schedule' and request.POST.get('start'):
        start = parse_datetime(request.POST.get('start'))
        if start and timezone.is_naive(start):
            start = timezone.make_aware(start, timezone.get_current_timezone())
        if not start:
            start = timezone.now() + timedelta(minutes=1)
    else:
        start = timezone.now() + timedelta(minutes=1)

    try:
        interval_minutes = max(int(request.POST.get('interval_minutes', 5)), 0)
    except (TypeError, ValueError):
        interval_minutes = 5
    interval = timedelta(minutes=interval_minutes)

    # ── Coleta de vídeos: uploads + biblioteca ─────────────────
    video_names = []
    for f in request.FILES.getlist('videos'):
        video_names.append(default_storage.save(f'reels/{f.name}', f))
    for vid in library_video_ids:
        asset = MediaAsset.objects.filter(id=vid, owner=user, kind='video').first()
        if asset:
            video_names.append(asset.file.name)
            asset.used_count += 1
            asset.save(update_fields=['used_count'])

    # ── Capa (opcional) ────────────────────────────────────────
    cover_name = None
    if 'cover' in request.FILES:
        cover_name = default_storage.save(f'thumbnails/{request.FILES["cover"].name}', request.FILES['cover'])
    elif cover_library_id:
        ca = MediaAsset.objects.filter(id=cover_library_id, owner=user, kind='image').first()
        if ca:
            cover_name = ca.file.name

    # ── Validação ──────────────────────────────────────────────
    if not account_ids:
        messages.error(request, 'Selecione ao menos uma conta.')
        return redirect('publisher:composer')
    if not video_names:
        messages.error(request, 'Envie ou selecione ao menos um vídeo.')
        return redirect('publisher:composer')

    caption_set = None
    if caption_set_id:
        caption_set = CaptionSet.objects.filter(id=caption_set_id, owner=user).first()

    # A fila de cada conta é: cada vídeo × 'repeat', espaçados pelo intervalo.
    queue_per_account = []
    for vname in video_names:
        queue_per_account.extend([vname] * repeat)

    # ── Criação dos jobs (cada conta posta 1 item por intervalo) ─
    created = 0
    skipped = 0
    for account_id in account_ids:
        account = InstagramAccount.objects.filter(id=account_id, owner=user).first()
        if not account:
            continue
        for i, vname in enumerate(queue_per_account):
            when_dt = start + i * interval
            if end_at and when_dt > end_at:
                skipped += 1
                continue
            post = ScheduledPost(
                owner=user,
                account=account,
                post_type=post_type,
                caption=caption,
                caption_set=caption_set,
                share_to_feed=share_to_feed,
                status='queued',
                scheduled_for=when_dt,
            )
            post.video_file.name = vname
            if cover_name:
                post.thumbnail.name = cover_name
            post.save()
            created += 1

    when = 'agora' if mode == 'now' else 'no horário agendado'
    msg = f'{created} publicação(ões) enfileirada(s) para {len(account_ids)} conta(s) — começando {when}.'
    if skipped:
        msg += f' {skipped} ignorada(s) por passar do "Parar em".'
    messages.success(request, msg)
    return redirect('publisher:queue')


@login_required
def schedule(request):
    return render(request, 'publisher/schedule.html')

@login_required
def api_events(request):
    start = request.GET.get('start')
    end = request.GET.get('end')
    
    events = []
    qs = ScheduledPost.objects.filter(owner=request.user)
    
    if start and end:
        qs = qs.filter(scheduled_for__range=[start, end])
        
    for post in qs:
        color = '#a855f7'  # roxo = na fila
        if post.status == 'published':
            color = '#22c55e'
        elif post.status == 'failed':
            color = '#ef4444'
        elif post.status == 'processing':
            color = '#f59e0b'

        events.append({
            'id': post.id,
            'title': f"@{post.account.ig_username} · {post.get_post_type_display()}",
            'start': post.scheduled_for.isoformat(),
            'color': color,
            # NÃO usar a URL de remoção aqui: clicar no evento apagava o post
            # sem confirmação. Levamos para a fila, onde há ações explícitas.
            'url': '/publisher/',
            'extendedProps': {
                'status': post.get_status_display(),
                'caption': (post.caption or '')[:120],
            },
        })

    return JsonResponse(events, safe=False)
