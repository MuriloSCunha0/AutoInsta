from datetime import timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.core.files.storage import default_storage
from django.utils.dateparse import parse_datetime
from .models import ScheduledPost, PostLoop
from .forms import ScheduledPostForm
from apps.instagram.models import InstagramAccount
from apps.library.models import MediaAsset, CaptionSet, Audio
from django.http import JsonResponse
from django.utils import timezone

@login_required
def queue_list(request):
    from django.core.paginator import Paginator
    from django.db.models import Count

    status = (request.GET.get('status') or '').strip()
    base = ScheduledPost.objects.filter(owner=request.user).select_related('account')
    if status:
        base = base.filter(status=status)

    paginator = Paginator(base.order_by('-scheduled_for'), 100)
    page = paginator.get_page(request.GET.get('page'))

    contagens = {
        r['status']: r['n']
        for r in ScheduledPost.objects.filter(owner=request.user)
        .values('status').annotate(n=Count('id'))
    }
    # Lista pronta para os filtros: (chave, rótulo, quantidade)
    filtros = [(chave, rotulo, contagens.get(chave, 0))
               for chave, rotulo in ScheduledPost.STATUS_CHOICES]

    form = ScheduledPostForm()
    form.fields['account'].queryset = form.fields['account'].queryset.filter(owner=request.user)
    return render(request, 'publisher/queue.html', {
        'posts': page,
        'page_obj': page,
        'status_atual': status,
        'filtros': filtros,
        'total_filtrado': paginator.count,
        'form': form,
        'accounts': InstagramAccount.objects.filter(owner=request.user),
    })

@login_required
def add_post(request):
    """Agenda uma publicação para UMA OU MAIS contas de uma vez."""
    if request.method != 'POST':
        return redirect('publisher:queue')

    user = request.user
    account_ids = request.POST.getlist('accounts')
    post_type = request.POST.get('post_type', 'REELS')
    caption = (request.POST.get('caption') or '').strip()
    quando_raw = request.POST.get('scheduled_for')
    arquivo = request.FILES.get('video_file')

    if not account_ids:
        messages.error(request, 'Selecione ao menos uma conta.')
        return redirect('publisher:queue')
    if not arquivo:
        messages.error(request, 'Envie a mídia da publicação.')
        return redirect('publisher:queue')

    quando = parse_datetime(quando_raw) if quando_raw else None
    if quando and timezone.is_naive(quando):
        quando = timezone.make_aware(quando, timezone.get_current_timezone())
    if not quando:
        quando = timezone.now() + timedelta(minutes=1)

    # Salva o arquivo UMA vez e referencia em todos os posts (sem duplicar).
    nome_arquivo = default_storage.save(f'reels/{arquivo.name}', arquivo)

    criados = 0
    for acc_id in account_ids:
        conta = InstagramAccount.objects.filter(id=acc_id, owner=user).first()
        if not conta:
            continue
        post = ScheduledPost(
            owner=user,
            account=conta,
            post_type=post_type,
            caption=caption,
            status='queued',
            scheduled_for=quando,
        )
        post.video_file.name = nome_arquivo
        post.save()
        criados += 1

    messages.success(request, f'{criados} publicação(ões) agendada(s).')
    return redirect('publisher:queue')
    
@login_required
def remove_post(request, post_id):
    post = get_object_or_404(ScheduledPost, id=post_id, owner=request.user)
    post.delete()
    return redirect('publisher:queue')


@login_required
@require_POST
def bulk_posts(request):
    """Ação em massa sobre posts selecionados: reprocessar ou excluir."""
    acao = request.POST.get('acao')

    # "Selecionar TODOS" manda uma flag (+ o filtro atual) em vez de milhares de
    # campos — era isso que estourava o limite do Django e devolvia HTTP 400.
    if request.POST.get('todos') == '1':
        qs = ScheduledPost.objects.filter(owner=request.user)
        status_filtro = (request.POST.get('status') or '').strip()
        if status_filtro:
            qs = qs.filter(status=status_filtro)
    else:
        qs = ScheduledPost.objects.filter(id__in=request.POST.getlist('post_ids'),
                                          owner=request.user)
    n = qs.count()

    if acao == 'excluir':
        qs.delete()
        messages.success(request, f'{n} publicação(ões) excluída(s).')
    elif acao == 'reprocessar':
        # Volta para a fila para agora, zerando o contador de tentativas.
        qs.update(status='queued', scheduled_for=timezone.now(),
                  retry_count=0, error_message='')
        messages.success(request, f'{n} publicação(ões) recolocada(s) na fila.')
    elif acao == 'forcar':
        # Forçar: publica AGORA, ignorando throttle, cooldown e limite diário
        # (como no Murphy). A Meta ainda pode recusar por volume real.
        # Teto por acionamento: disparar milhares de uma vez inunda a API da
        # Meta — que é exatamente o que causa bloqueio.
        LIMITE_FORCAR = 100
        total_pedido = n
        qs = qs.order_by('scheduled_for')[:LIMITE_FORCAR]
        n = len(qs)

        from .tasks import publish_reel
        contas_limpas = set()
        for post in qs.select_related('account'):
            post.status = 'processing'
            post.scheduled_for = timezone.now()
            post.retry_count = 0
            post.error_message = ''
            post.save(update_fields=['status', 'scheduled_for', 'retry_count', 'error_message'])
            # Limpa o cooldown da conta para a força valer.
            if post.account_id not in contas_limpas and post.account.rate_limited_until:
                post.account.rate_limited_until = None
                post.account.save(update_fields=['rate_limited_until'])
                contas_limpas.add(post.account_id)
            publish_reel.delay(post.id)
        msg = f'{n} publicação(ões) FORÇADA(S) agora — a Meta ainda pode limitar por volume real.'
        if total_pedido > n:
            msg += (f' Das {total_pedido} selecionadas, forçamos as {n} mais antigas '
                    f'(teto por vez, para não inundar a API e arriscar bloqueio). '
                    'Repita a ação para continuar.')
        messages.warning(request, msg)
    else:
        messages.error(request, 'Ação inválida.')

    destino = request.POST.get('next') or 'publisher:queue'
    return redirect(destino)


@login_required
def toggle_pause(request):
    """Pausa/retoma TODAS as filas do usuário (posts e loops)."""
    request.user.publishing_paused = not request.user.publishing_paused
    request.user.save(update_fields=['publishing_paused'])
    if request.user.publishing_paused:
        messages.warning(request, 'Filas PAUSADAS. Nada será publicado até você retomar.')
    else:
        messages.success(request, 'Filas retomadas. As publicações voltam a sair.')
    return redirect(request.POST.get('next') or request.META.get('HTTP_REFERER') or 'publisher:queue')

@login_required
def loops(request):
    from apps.library.models import MediaFolder
    loops_list = PostLoop.objects.filter(owner=request.user).select_related('account', 'folder')
    return render(request, 'publisher/loops.html', {
        'loops': loops_list,
        'accounts': InstagramAccount.objects.filter(owner=request.user),
        'folders': MediaFolder.objects.filter(owner=request.user),
        'post_types': ScheduledPost.TYPE_CHOICES,
    })


@login_required
def add_loop(request):
    """Cria um Loop. Aceita várias contas — um loop por conta."""
    if request.method != 'POST':
        return redirect('publisher:loops')

    from apps.library.models import MediaFolder

    user = request.user
    account_ids = request.POST.getlist('accounts')
    folder_id = (request.POST.get('folder') or '').strip()
    arquivo = request.FILES.get('video_file')

    if not account_ids:
        messages.error(request, 'Selecione ao menos uma conta.')
        return redirect('publisher:loops')

    folder = MediaFolder.objects.filter(id=folder_id, owner=user).first() if folder_id else None
    if not folder and not arquivo:
        messages.error(request, 'Escolha uma pasta da biblioteca ou envie um arquivo.')
        return redirect('publisher:loops')

    try:
        intervalo = max(int(request.POST.get('interval_minutes', 1440)), 1)
    except (TypeError, ValueError):
        intervalo = 1440

    nome_arquivo = default_storage.save(f'loops/{arquivo.name}', arquivo) if arquivo else ''

    clean_mode = request.POST.get('clean_mode', 'light')
    if clean_mode not in ('none', 'light', 'ultra'):
        clean_mode = 'light'

    criados = 0
    for acc_id in account_ids:
        conta = InstagramAccount.objects.filter(id=acc_id, owner=user).first()
        if not conta:
            continue
        loop = PostLoop(
            owner=user,
            account=conta,
            post_type=request.POST.get('post_type', 'REELS'),
            folder=folder,
            caption=(request.POST.get('caption') or '').strip(),
            interval_minutes=intervalo,
            share_to_feed=request.POST.get('grade', 'grade') == 'grade',
            clean_mode=clean_mode,
            is_active=True,
        )
        if nome_arquivo:
            loop.video_file.name = nome_arquivo
        loop.save()
        criados += 1

    alvo = f'pasta "{folder.name}"' if folder else 'arquivo enviado'
    messages.success(request, f'{criados} loop(s) criado(s) a partir da {alvo}.')
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
    posts = ScheduledPost.objects.filter(owner=request.user, post_type='STORY').select_related('account')
    form = ScheduledPostForm(initial={'post_type': 'STORY'})
    form.fields['account'].queryset = form.fields['account'].queryset.filter(owner=request.user)
    return render(request, 'publisher/stories.html', {
        'posts': posts,
        'form': form,
        'accounts': InstagramAccount.objects.filter(owner=request.user),
    })

@login_required
def composer(request):
    """Composer real: publica/agenda em massa (várias contas × várias mídias)."""
    user = request.user

    if request.method == 'POST':
        return _composer_submit(request)

    context = {
        'accounts': InstagramAccount.objects.filter(owner=user),
        # Story e Post de feed aceitam IMAGEM também — não filtramos só vídeo.
        'library_media': MediaAsset.objects.filter(owner=user),
        'library_covers': MediaAsset.objects.filter(owner=user, kind='image'),
        'caption_sets': CaptionSet.objects.filter(owner=user),
        'audios': Audio.objects.filter(owner=user),
        'post_types': ScheduledPost.TYPE_CHOICES,
    }
    return render(request, 'publisher/composer.html', context)


def _composer_submit(request):
    user = request.user

    account_ids = request.POST.getlist('accounts')
    library_media_ids = request.POST.getlist('library_media')
    caption = (request.POST.get('caption') or '').strip()
    caption_set_id = request.POST.get('caption_set')
    post_type = request.POST.get('post_type', 'REELS')
    cover_library_id = request.POST.get('cover_library')

    # Reels + grade (share_to_feed) — valor 'grade' liga; 'reels' deixa só na aba.
    share_to_feed = request.POST.get('grade', 'grade') == 'grade'
    story_link = (request.POST.get('story_link') or '').strip()

    # Modo de limpeza/diversificação do arquivo (none/light/ultra).
    clean_mode = request.POST.get('clean_mode', 'light')
    if clean_mode not in ('none', 'light', 'ultra'):
        clean_mode = 'light'

    # Trilha da aba Áudios (só quando "Colocar música" está marcado).
    audio = None
    if request.POST.get('audio_mode') == 'music':
        audio = Audio.objects.filter(id=request.POST.get('audio'), owner=user).first()

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

    # ── Coleta de mídias: uploads + biblioteca (vídeo OU imagem) ─
    video_names = []
    for f in request.FILES.getlist('videos'):
        video_names.append(default_storage.save(f'reels/{f.name}', f))
    for mid in library_media_ids:
        asset = MediaAsset.objects.filter(id=mid, owner=user).first()
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
        messages.error(request, 'Envie ou selecione ao menos uma mídia.')
        return redirect('publisher:composer')

    # Story com link exige sessão da engine (a API oficial não tem sticker de
    # link). Avisamos ANTES de enfileirar, em vez de falhar na publicação.
    if post_type == 'STORY' and story_link:
        sem_sessao = InstagramAccount.objects.filter(
            id__in=account_ids, owner=user, session_blob__isnull=True
        ).values_list('ig_username', flat=True)
        if sem_sessao:
            messages.error(
                request,
                'Story com link precisa da conta conectada por sessão/senha '
                f'(a API oficial não permite link). Sem sessão: @{", @".join(sem_sessao)}. '
                'Publique o Story sem link ou conecte essas contas pela aba Entrar.'
            )
            return redirect('publisher:composer')

    caption_set = None
    if caption_set_id:
        caption_set = CaptionSet.objects.filter(id=caption_set_id, owner=user).first()

    # A fila de cada conta repete o CONJUNTO de mídias, não cada mídia em
    # seguida. Com 3 vídeos e repetir=3: v1,v2,v3,v1,v2,v3,v1,v2,v3 —
    # e não v1,v1,v1,v2,v2,v2 (que postaria o mesmo vídeo duas vezes seguidas).
    queue_per_account = list(video_names) * repeat

    # ── Limite diário por conta ───────────────────────────────
    respeitar_limite = request.POST.get('limite_diario', 'respeitar') == 'respeitar'

    from collections import defaultdict
    usados_no_dia = defaultdict(int)
    if respeitar_limite:
        # Conta o que JÁ está agendado, para o teto valer de verdade.
        for p in ScheduledPost.objects.filter(
            owner=user, account_id__in=account_ids, status__in=('queued', 'processing')
        ).only('account_id', 'scheduled_for'):
            usados_no_dia[(p.account_id, timezone.localtime(p.scheduled_for).date())] += 1

    def encaixar_no_limite(quando, conta):
        """Empurra para o próximo dia enquanto o teto do dia estiver cheio."""
        limite = conta.daily_post_limit or 0
        if not respeitar_limite or limite <= 0:
            return quando, False
        adiado = False
        while usados_no_dia[(conta.id, timezone.localtime(quando).date())] >= limite:
            quando = quando + timedelta(days=1)
            adiado = True
        usados_no_dia[(conta.id, timezone.localtime(quando).date())] += 1
        return quando, adiado

    # ── Criação dos jobs (cada conta posta 1 item por intervalo) ─
    created = 0
    skipped = 0
    adiados = 0
    for account_id in account_ids:
        account = InstagramAccount.objects.filter(id=account_id, owner=user).first()
        if not account:
            continue
        for i, vname in enumerate(queue_per_account):
            when_dt = start + i * interval
            when_dt, foi_adiado = encaixar_no_limite(when_dt, account)
            if foi_adiado:
                adiados += 1
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
                story_link=story_link if post_type == 'STORY' else '',
                clean_mode=clean_mode,
                audio=audio,
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
    if adiados:
        msg += f' {adiados} remanejada(s) para os dias seguintes pelo limite diário.'
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
