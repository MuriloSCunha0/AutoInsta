from datetime import timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.core.files.storage import default_storage
from django.utils.dateparse import parse_datetime
from .models import ScheduledPost, PostLoop, PostQueue
from .forms import ScheduledPostForm
from apps.instagram.models import InstagramAccount
from apps.library.models import MediaAsset, CaptionSet, Audio
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone


@csrf_exempt
@require_POST
def internal_upload_media(request):
    """Recebe do BRAÇO um arquivo de mídia PROCESSADO e o grava no MEDIA_ROOT do
    painel, para a Graph API baixá-lo pela URL pública (a Meta baixa a mídia por
    URL). Server-to-server: sem login, protegido por token compartilhado
    (MEDIA_UPLOAD_TOKEN). Aceita SÓ dentro de 'processed/' (anti path traversal)."""
    import os
    from django.conf import settings
    tok_cfg = getattr(settings, 'MEDIA_UPLOAD_TOKEN', '') or ''
    if not tok_cfg or request.headers.get('X-Upload-Token', '') != tok_cfg:
        return HttpResponseForbidden('token invalido')
    relname = (request.POST.get('relname') or '').strip().replace('\\', '/').lstrip('/')
    arquivo = request.FILES.get('file')
    if not relname or not arquivo:
        return JsonResponse({'ok': False, 'error': 'faltou relname/file'}, status=400)
    if not relname.startswith('processed/') or '..' in relname:
        return HttpResponseForbidden('relname nao permitido')
    dest = os.path.join(settings.MEDIA_ROOT, relname.replace('/', os.sep))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'wb') as out:
        for chunk in arquivo.chunks():
            out.write(chunk)
    return JsonResponse({'ok': True, 'relname': relname})

@login_required
def queue_list(request):
    from django.core.paginator import Paginator
    from django.db.models import Count, Q

    status = (request.GET.get('status') or '').strip()
    fila_id = (request.GET.get('queue') or '').strip()
    conta_id = (request.GET.get('account') or '').strip()
    pasta_id = (request.GET.get('pasta') or '').strip()

    # A fila é o que está POR FAZER. Publicado saiu da fila: virou histórico,
    # que tem tela própria (publisher:historico).
    base = (ScheduledPost.objects.filter(owner=request.user,
                                         status__in=ScheduledPost.STATUS_ATIVOS)
            .select_related('account', 'queue'))
    if status in ScheduledPost.STATUS_ATIVOS:
        base = base.filter(status=status)
    else:
        status = ''
    # Filtro por PASTA: posts das contas daquela pasta.
    if pasta_id == 'none':
        base = base.filter(account__pasta__isnull=True)
    elif pasta_id:
        base = base.filter(account__pasta_id=pasta_id)
    # Filtro por conta: mostra só o que é daquela conta (posts e filas).
    if conta_id:
        base = base.filter(account_id=conta_id)
    if fila_id:
        base = base.filter(queue_id=fila_id)

    # Mais PRÓXIMAS de postar primeiro; as mais distantes ficam no fim.
    paginator = Paginator(base.order_by('scheduled_for'), 100)
    page = paginator.get_page(request.GET.get('page'))

    # Agrupa a página por DIA: Hoje, Amanhã, depois as datas — na ordem de
    # postagem. Fica claro o que sai hoje e o que fica para os próximos dias.
    from datetime import timedelta
    hoje = timezone.localdate()
    grupos_dia = []
    for p in page.object_list:
        d = timezone.localtime(p.scheduled_for).date() if p.scheduled_for else hoje
        if d == hoje:
            rotulo = 'Hoje'
        elif d == hoje + timedelta(days=1):
            rotulo = 'Amanhã'
        elif d < hoje:
            rotulo = 'Atrasados'
        else:
            rotulo = d.strftime('%d/%m/%Y')
        if not grupos_dia or grupos_dia[-1]['rotulo'] != rotulo:
            grupos_dia.append({'rotulo': rotulo, 'posts': []})
        grupos_dia[-1]['posts'].append(p)

    contagens = {
        r['status']: r['n']
        for r in ScheduledPost.objects.filter(owner=request.user)
        .values('status').annotate(n=Count('id'))
    }
    # Filtros: (chave, rótulo, quantidade) — só os status que ficam na fila.
    filtros = [(chave, rotulo, contagens.get(chave, 0))
               for chave, rotulo in ScheduledPost.STATUS_CHOICES
               if chave in ScheduledPost.STATUS_ATIVOS]

    # As filas exibidas seguem o filtro de conta, para a tela ficar coerente.
    filas = PostQueue.objects.filter(owner=request.user).select_related('account')
    if conta_id:
        filas = filas.filter(account_id=conta_id)

    # Contas com o total pendente de cada uma, para o seletor de conta.
    contas = (InstagramAccount.objects.filter(owner=request.user)
              .annotate(pendentes=Count('scheduledpost',
                                        filter=Q(scheduledpost__status__in=ScheduledPost.STATUS_ATIVOS)))
              .order_by('-pendentes', 'ig_username'))

    # Pastas para filtrar a fila por grupo de contas.
    from apps.instagram.models import Pasta
    pastas = Pasta.objects.filter(owner=request.user)

    form = ScheduledPostForm()
    form.fields['account'].queryset = form.fields['account'].queryset.filter(owner=request.user)
    return render(request, 'publisher/queue.html', {
        'posts': page,
        'grupos_dia': grupos_dia,
        'page_obj': page,
        'status_atual': status,
        'filtros': filtros,
        'filas': filas,
        'fila_atual': fila_id,
        'conta_atual': conta_id,
        'pastas': pastas,
        'pasta_atual': pasta_id,
        'total_filtrado': paginator.count,
        'total_publicados': contagens.get('published', 0),
        'form': form,
        'accounts': contas,
    })


@login_required
def historico(request):
    """Publicados: histórico, separado da fila (são dados diferentes)."""
    from django.core.paginator import Paginator

    conta_id = (request.GET.get('account') or '').strip()
    posts = (ScheduledPost.objects.filter(owner=request.user, status='published')
             .select_related('account', 'queue'))
    if conta_id:
        posts = posts.filter(account_id=conta_id)

    paginator = Paginator(posts.order_by('-published_at'), 100)
    page = paginator.get_page(request.GET.get('page'))

    return render(request, 'publisher/historico.html', {
        'posts': page,
        'page_obj': page,
        'total_filtrado': paginator.count,
        'conta_atual': conta_id,
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
        quando = timezone.now()

    # Salva o arquivo UMA vez e referencia em todos os posts (sem duplicar).
    from apps.core_utils import nome_seguro
    nome_arquivo = default_storage.save(f'reels/{nome_seguro(arquivo.name)}', arquivo)

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

        # ESCOPO: "todos" tem de significar "todos os que a tela mostrava".
        # Sem isto, um "excluir todas" na fila levaria junto o histórico
        # inteiro de publicados — que a tela nem exibia.
        escopo = (request.POST.get('escopo') or 'fila').strip()
        if escopo in ('historico', 'publicados'):
            qs = qs.filter(status='published')
        elif escopo == 'falhas':
            qs = qs.filter(status='failed')
        elif escopo == 'fila':
            qs = qs.filter(status__in=['draft', 'queued', 'processing'])
        else:
            qs = qs.filter(status__in=ScheduledPost.STATUS_ATIVOS)

        status_filtro = (request.POST.get('status') or '').strip()
        if status_filtro:
            qs = qs.filter(status=status_filtro)
        tipo_filtro = (request.POST.get('post_type') or '').strip()
        if tipo_filtro:
            qs = qs.filter(post_type=tipo_filtro)
        fila_filtro = (request.POST.get('queue') or '').strip()
        if fila_filtro:
            qs = qs.filter(queue_id=fila_filtro)
        # Filtro por conta: "selecionar todas" tem de respeitar a conta aberta,
        # senão a ação atingiria posts de outras contas que a tela nem mostrava.
        conta_filtro = (request.POST.get('account') or '').strip()
        if conta_filtro:
            qs = qs.filter(account_id=conta_filtro)
        # Idem para o filtro de pasta.
        pasta_filtro = (request.POST.get('pasta') or '').strip()
        if pasta_filtro == 'none':
            qs = qs.filter(account__pasta__isnull=True)
        elif pasta_filtro:
            qs = qs.filter(account__pasta_id=pasta_filtro)
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
        # "Forçar": reancora a fila de cada conta em AGORA, mas ESPAÇADA 30/30
        # (a 1ª sai agora, a 2ª em +30 min, a 3ª +60...). Nunca em burst: postar a
        # sequência toda em segundos é lido pela Meta como atividade coordenada e
        # INVALIDA tokens (visto em produção). Também limpa o cooldown para as
        # contas saírem logo. Se a conta publicou algo há pouco, o dispatcher ainda
        # segura a 1ª até 30 min depois dessa última (piso anti-rajada real).
        from datetime import timedelta
        from django.conf import settings as _s
        gap_padrao = getattr(_s, 'MIN_INTERVALO_POST_MIN', 30)
        agora = timezone.now()
        limite_voo = agora - timedelta(minutes=15)
        qs = qs.exclude(status='processing', processing_since__gte=limite_voo)
        ids = list(qs.values_list('id', flat=True))
        n = len(ids)
        contas_ids = list(
            ScheduledPost.objects.filter(id__in=ids)
            .values_list('account_id', flat=True).distinct()
        )
        # Limpa o cooldown das contas envolvidas (para saírem logo), sem burst.
        InstagramAccount.objects.filter(
            id__in=contas_ids, rate_limited_until__isnull=False
        ).update(rate_limited_until=None)
        # Reancora cada conta em AGORA, espaçando pelo intervalo de cada post.
        for aid in contas_ids:
            posts = list(ScheduledPost.objects.filter(id__in=ids, account_id=aid)
                         .order_by('scheduled_for', 'created_at'))
            quando = agora
            for p in posts:
                p.scheduled_for = quando
                p.status = 'queued'
                p.retry_count = 0
                p.error_message = ''
                p.save(update_fields=['scheduled_for', 'status',
                                      'retry_count', 'error_message'])
                quando = quando + timedelta(minutes=(p.interval_minutes or gap_padrao))
        messages.warning(
            request,
            f'{n} publicação(ões) recolocada(s): a 1ª de cada conta sai AGORA e as '
            'seguintes de 30 em 30 min (espaçado, anti-bloqueio) — nunca tudo de uma vez.')
    else:
        messages.error(request, 'Ação inválida.')

    destino = request.POST.get('next') or 'publisher:queue'
    return redirect(destino)


@login_required
@require_POST
def toggle_queue_pause(request, queue_id):
    """Pausa/retoma UMA fila específica (as outras da conta seguem rodando)."""
    fila = get_object_or_404(PostQueue, id=queue_id, owner=request.user)
    fila.paused = not fila.paused
    fila.save(update_fields=['paused'])
    estado = 'pausada' if fila.paused else 'retomada'
    messages.success(request, f'Fila "{fila.name}" (@{fila.account.ig_username}) {estado}.')
    return redirect(request.POST.get('next') or 'publisher:queue')


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

    from apps.core_utils import nome_seguro
    nome_arquivo = default_storage.save(f'loops/{nome_seguro(arquivo.name)}', arquivo) if arquivo else ''

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
    from django.core.paginator import Paginator
    from django.db.models import Count, Q

    todos = (ScheduledPost.objects.filter(owner=request.user, post_type='STORY')
             .select_related('account'))
    # Contadores das abas (Na fila / Publicados / Falhas).
    counts = todos.aggregate(
        fila=Count('id', filter=Q(status__in=['draft', 'queued', 'processing'])),
        publicados=Count('id', filter=Q(status='published')),
        falhas=Count('id', filter=Q(status='failed')),
    )

    tab = (request.GET.get('tab') or 'fila').strip()
    if tab == 'publicados':
        base = todos.filter(status='published').order_by('-published_at')
    elif tab == 'falhas':
        base = todos.filter(status='failed').order_by('-created_at')
    else:
        tab = 'fila'
        base = todos.filter(status__in=['draft', 'queued', 'processing']).order_by('scheduled_for')

    paginator = Paginator(base, 60)
    page = paginator.get_page(request.GET.get('page'))

    form = ScheduledPostForm(initial={'post_type': 'STORY'})
    form.fields['account'].queryset = form.fields['account'].queryset.filter(owner=request.user)
    from apps.instagram.models import Pasta
    contas_story = (InstagramAccount.objects.filter(owner=request.user)
                    .select_related('pasta').order_by('pasta__name', 'ig_username'))
    return render(request, 'publisher/stories.html', {
        'posts': page,
        'page_obj': page,
        'total_filtrado': paginator.count,
        'form': form,
        'accounts': contas_story,
        'pastas': Pasta.objects.filter(owner=request.user),
        'tab': tab,
        'counts': counts,
    })

@login_required
def composer(request):
    """Composer real: publica/agenda em massa (várias contas × várias mídias)."""
    user = request.user

    if request.method == 'POST':
        return _composer_submit(request)

    # Cada conta com a contagem do que já está na fila — o disparo em massa
    # precisa distinguir "já tem agendado" de "sem nada", não só ativa/inativa.
    from django.db.models import Count, Q
    contas = (InstagramAccount.objects.filter(owner=user)
              .annotate(pendentes=Count('scheduledpost',
                                        filter=Q(scheduledpost__status__in=['queued', 'processing'])))
              .order_by('pendentes', 'ig_username'))  # as sem agendados primeiro

    context = {
        'accounts': contas,
        # Story e Post de feed aceitam IMAGEM também — não filtramos só vídeo.
        'library_media': MediaAsset.objects.filter(owner=user),
        'library_covers': MediaAsset.objects.filter(owner=user, kind='image'),
        'caption_sets': CaptionSet.objects.filter(owner=user),
        'audios': Audio.objects.filter(owner=user),
        'post_types': ScheduledPost.TYPE_CHOICES,
        # Nomes de fila já usados, para sugerir no campo.
        'filas_existentes': sorted(set(
            PostQueue.objects.filter(owner=user).values_list('name', flat=True))),
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

    # Editor visual de Story (texto queimado + posição do link).
    def _f(nome, padrao):
        try:
            return float(request.POST.get(nome))
        except (TypeError, ValueError):
            return padrao
    story_cfg = {
        'story_text': (request.POST.get('story_text') or '').strip()[:200],
        'story_text_color': (request.POST.get('story_text_color') or '#ffffff').strip()[:20],
        'story_text_bg': (request.POST.get('story_text_bg') or 'dark').strip()[:10],
        'story_text_size': max(8, min(int(_f('story_text_size', 28)), 200)),
        'story_text_x': min(1.0, max(0.0, _f('story_text_x', 0.5))),
        'story_text_y': min(1.0, max(0.0, _f('story_text_y', 0.45))),
        'story_link_x': min(1.0, max(0.0, _f('story_link_x', 0.5))),
        'story_link_y': min(1.0, max(0.0, _f('story_link_y', 0.82))),
        'story_link_label': (request.POST.get('story_link_label') or 'CLIQUE AQUI').strip()[:40],
    }

    # Modo de limpeza/diversificação do arquivo (none/light/ultra).
    clean_mode = request.POST.get('clean_mode', 'light')
    if clean_mode not in ('none', 'light', 'ultra'):
        clean_mode = 'light'

    # Trilha da aba Áudios (só quando "Colocar música" está marcado).
    audio = None
    if request.POST.get('audio_mode') == 'music':
        audio = Audio.objects.filter(id=request.POST.get('audio'), owner=user).first()

    # Nome da fila: permite ter VÁRIAS filas por conta rodando em paralelo.
    nome_fila = (request.POST.get('queue_name') or '').strip()

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
            start = timezone.now()
    else:
        # "Agora": sai já no próximo ciclo do dispatcher (segundos), não +1min.
        start = timezone.now()

    try:
        interval_minutes = max(int(request.POST.get('interval_minutes', 5)), 0)
    except (TypeError, ValueError):
        interval_minutes = 5
    interval = timedelta(minutes=interval_minutes)

    # ── Coleta de mídias: uploads + biblioteca (vídeo OU imagem) ─
    from apps.core_utils import nome_seguro

    video_names = []
    for f in request.FILES.getlist('videos'):
        # Nome ASCII: a Meta baixa a mídia pela URL e falha com acento no nome.
        video_names.append(default_storage.save(f'reels/{nome_seguro(f.name)}', f))
    for mid in library_media_ids:
        asset = MediaAsset.objects.filter(id=mid, owner=user).first()
        if asset:
            video_names.append(asset.file.name)
            asset.used_count += 1
            asset.save(update_fields=['used_count'])

    # ── Capa (opcional) ────────────────────────────────────────
    cover_name = None
    if 'cover' in request.FILES:
        cover_name = default_storage.save(
            f'thumbnails/{nome_seguro(request.FILES["cover"].name)}', request.FILES['cover'])
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
        # Sem sessão utilizável = sem session_blob OU sessão expirada (híbrido:
        # conta com token mas sessão caída não consegue link até recolar).
        from django.db.models import Q
        sem_sessao = InstagramAccount.objects.filter(
            Q(session_blob__isnull=True) | Q(sessao_expirada=True),
            id__in=account_ids, owner=user,
        ).values_list('ig_username', flat=True)
        if sem_sessao:
            messages.error(
                request,
                'Story com link precisa da conta com sessão ativa (a API oficial não '
                f'permite link). Sem sessão: @{", @".join(sem_sessao)}. '
                'Recole o sessionid dessas contas no card, ou publique o Story sem link.'
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
    por_conta = []  # [(username, quantos)] para o resumo da campanha
    n_contas = max(len(account_ids), 1)
    # Escalonamento ENTRE contas: espalha cada conta DENTRO do intervalo para que
    # não publiquem todas no MESMO instante. Antes, media[i] de TODAS as contas
    # caía no mesmo horário (fila "na mesma hora" + mini-burst). Agora cada conta
    # ganha um deslocamento próprio.
    if interval and interval.total_seconds() > 0:
        gap_conta = interval / n_contas
    else:
        gap_conta = timedelta(seconds=90)   # sem intervalo: 90s entre contas

    # NÃO sobrepor lotes na MESMA conta: se a conta já tem fila agendada, a nova
    # campanha continua DEPOIS do último item dela (+intervalo). Sem isto, rodar
    # o composer/campanhas várias vezes empilhava posts da mesma conta com poucos
    # minutos de diferença (bug: agendado 30/30 min mas saía 8 em 8) porque todo
    # lote começava "agora".
    from django.db.models import Max as _Max
    ultimo_por_conta = {
        r['account_id']: r['m']
        for r in ScheduledPost.objects.filter(
            owner=user, account_id__in=account_ids,
            status__in=('queued', 'processing', 'draft')
        ).values('account_id').annotate(m=_Max('scheduled_for'))
    }

    for pos_conta, account_id in enumerate(account_ids):
        account = InstagramAccount.objects.filter(id=account_id, owner=user).first()
        if not account:
            continue
        offset_conta = gap_conta * pos_conta
        # Base desta conta: começa em (agora + offset) OU logo após o que ela já
        # tem agendado (+intervalo), o que for MAIS TARDE — nunca sobrepõe.
        base_conta = start + offset_conta
        ja = ultimo_por_conta.get(account_id)
        if ja and interval and interval.total_seconds() > 0 and (ja + interval) > base_conta:
            base_conta = ja + interval

        # Cada conta ganha (ou reaproveita) a fila com esse nome.
        fila = None
        if nome_fila:
            fila, _ = PostQueue.objects.get_or_create(
                owner=user, account=account, name=nome_fila)

        criados_conta = 0
        for i, vname in enumerate(queue_per_account):
            when_dt = base_conta + i * interval
            when_dt, foi_adiado = encaixar_no_limite(when_dt, account)
            if foi_adiado:
                adiados += 1
            if end_at and when_dt > end_at:
                skipped += 1
                continue
            post = ScheduledPost(
                owner=user,
                account=account,
                queue=fila,
                post_type=post_type,
                caption=caption,
                caption_set=caption_set,
                share_to_feed=share_to_feed,
                story_link=story_link if post_type == 'STORY' else '',
                clean_mode=clean_mode,
                audio=audio,
                status='queued',
                scheduled_for=when_dt,
                interval_minutes=interval_minutes,
                **(story_cfg if post_type == 'STORY' else {}),
            )
            post.video_file.name = vname
            if cover_name:
                post.thumbnail.name = cover_name
            post.save()
            created += 1
            criados_conta += 1

        if criados_conta:
            por_conta.append((account.ig_username, criados_conta))

    # Resumo da campanha para a tela de sucesso (como no Murphy).
    total_contas = InstagramAccount.objects.filter(owner=user, status='active').count()
    n_contas = len(por_conta)
    request.session['campanha_ok'] = {
        'created': created,
        'skipped': skipped,
        'adiados': adiados,
        'n_contas': n_contas,
        'n_midias': len(video_names),
        'tipo': dict(ScheduledPost.TYPE_CHOICES).get(post_type, post_type),
        'modo': 'Todas as contas' if n_contas >= total_contas and total_contas else f'{n_contas} conta(s)',
        'intervalo': interval_minutes,
        'entradas': len(queue_per_account),
        'repeticao': repeat,
        'limite': 'Ignorado' if not respeitar_limite else 'Respeitado',
        'fila': nome_fila or '',
        'por_conta': por_conta[:60],
        'inicio': 'agora' if mode == 'now' else 'no horário agendado',
    }
    return redirect('publisher:campanha_ok')


@login_required
def campanha_ok(request):
    """Tela de sucesso da campanha (resumo do que foi agendado)."""
    resumo = request.session.pop('campanha_ok', None)
    if not resumo:
        return redirect('publisher:queue')
    return render(request, 'publisher/campanha_ok.html', {'r': resumo})


@login_required
def schedule(request):
    from .models import AgendaSemanal
    agendas = (AgendaSemanal.objects.filter(owner=request.user)
               .prefetch_related('accounts').order_by('hora'))
    return render(request, 'publisher/schedule.html', {
        'agendas': agendas,
        'accounts': InstagramAccount.objects.filter(owner=request.user).order_by('ig_username'),
        'dias': AgendaSemanal.DIAS,
    })


@login_required
@require_POST
def criar_agenda(request):
    """Cria um plano semanal recorrente (conteúdo + contas + dias + horário)."""
    from .models import AgendaSemanal
    from apps.core_utils import nome_seguro

    accounts_ids = request.POST.getlist('accounts')
    weekdays = [d for d in request.POST.getlist('weekdays') if d in '0123456']
    hora = (request.POST.get('hora') or '').strip()
    media = request.FILES.get('media')

    if not (accounts_ids and weekdays and hora and media):
        messages.error(request, 'Escolha ao menos 1 conta, 1 dia, o horário e a mídia.')
        return redirect('publisher:schedule')

    try:
        espac = max(1, int(request.POST.get('espacamento_min') or 20))
    except ValueError:
        espac = 20

    media.name = nome_seguro(media.name)
    ag = AgendaSemanal.objects.create(
        owner=request.user,
        name=(request.POST.get('name') or '').strip()[:80],
        weekdays=','.join(weekdays),
        hora=hora,
        post_type=(request.POST.get('post_type') or 'STORY'),
        video_file=media,
        caption=(request.POST.get('caption') or '').strip(),
        # Checkbox: marcado envia 'grade'; desmarcado não envia nada -> False.
        share_to_feed=request.POST.get('grade') == 'grade',
        clean_mode=(request.POST.get('clean_mode') or 'light'),
        story_link=(request.POST.get('story_link') or '').strip(),
        story_link_label=(request.POST.get('story_link_label') or 'CLIQUE AQUI').strip()[:40],
        espacamento_min=espac,
    )
    ag.accounts.set(InstagramAccount.objects.filter(id__in=accounts_ids, owner=request.user))
    messages.success(request, f'Plano recorrente criado ({ag.accounts.count()} conta(s), {ag.dias_label}).')
    return redirect('publisher:schedule')


@login_required
def toggle_agenda(request, agenda_id):
    from .models import AgendaSemanal
    ag = get_object_or_404(AgendaSemanal, id=agenda_id, owner=request.user)
    ag.active = not ag.active
    ag.save(update_fields=['active'])
    return redirect('publisher:schedule')


@login_required
def excluir_agenda(request, agenda_id):
    from .models import AgendaSemanal
    ag = get_object_or_404(AgendaSemanal, id=agenda_id, owner=request.user)
    ag.delete()
    messages.success(request, 'Plano recorrente removido.')
    return redirect('publisher:schedule')


@login_required
@require_POST
def editar_agenda(request):
    """Edita um plano recorrente. A mídia é opcional (vazio = mantém a atual)."""
    from .models import AgendaSemanal
    from apps.core_utils import nome_seguro

    ag = get_object_or_404(AgendaSemanal, id=request.POST.get('agenda_id'), owner=request.user)
    accounts_ids = request.POST.getlist('accounts')
    weekdays = [d for d in request.POST.getlist('weekdays') if d in '0123456']
    hora = (request.POST.get('hora') or '').strip()
    if not (accounts_ids and weekdays and hora):
        messages.error(request, 'Escolha ao menos 1 conta, 1 dia e o horário.')
        return redirect('publisher:schedule')

    ag.name = (request.POST.get('name') or '').strip()[:80]
    ag.weekdays = ','.join(weekdays)
    ag.hora = hora
    ag.post_type = request.POST.get('post_type') or 'STORY'
    ag.caption = (request.POST.get('caption') or '').strip()
    # Checkbox: marcado envia 'grade'; desmarcado não envia nada -> False.
    ag.share_to_feed = request.POST.get('grade') == 'grade'
    ag.clean_mode = request.POST.get('clean_mode') or 'light'
    ag.story_link = (request.POST.get('story_link') or '').strip()
    ag.story_link_label = (request.POST.get('story_link_label') or 'CLIQUE AQUI').strip()[:40]
    try:
        ag.espacamento_min = max(1, int(request.POST.get('espacamento_min') or 20))
    except ValueError:
        pass
    media = request.FILES.get('media')
    if media:
        media.name = nome_seguro(media.name)
        ag.video_file = media
    ag.save()
    ag.accounts.set(InstagramAccount.objects.filter(id__in=accounts_ids, owner=request.user))
    messages.success(request, 'Plano atualizado.')
    return redirect('publisher:schedule')

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
