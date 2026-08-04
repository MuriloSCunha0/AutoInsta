from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.conf import settings
from .models import CaptionSet, Caption, Audio, MediaFolder, MediaAsset, ProfileDownload

@login_required
def captions_list(request):
    caption_sets = CaptionSet.objects.filter(owner=request.user)
    return render(request, 'library/captions.html', {'caption_sets': caption_sets})

@login_required
def add_caption(request):
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        description = (request.POST.get('description') or '').strip()
        text = (request.POST.get('text') or '').strip()
        hashtags = (request.POST.get('hashtags') or '').strip()
        if name:
            cs = CaptionSet.objects.create(owner=request.user, name=name, description=description)
            # Salva o TEXTO junto (antes só criava o conjunto vazio — o usuário
            # tinha de "Editar" e adicionar a variação, o que ninguém fazia, e a
            # legenda "não aparecia" no composer).
            if text:
                Caption.objects.create(caption_set=cs, text=text, hashtags=hashtags)
                messages.success(request, f'Legenda "{name}" salva.')
            else:
                messages.success(request, f'Conjunto "{name}" criado. Adicione a legenda em "Editar".')
    return redirect('library:captions')

@login_required
def delete_caption(request, caption_id):
    caption = get_object_or_404(CaptionSet, id=caption_id, owner=request.user)
    caption.delete()
    return redirect('library:captions')


@login_required
def edit_caption(request, caption_id):
    """Edita o conjunto de legendas e gerencia suas variações (spintax)."""
    cs = get_object_or_404(CaptionSet, id=caption_id, owner=request.user)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_set':
            name = (request.POST.get('name') or '').strip()
            if name:
                cs.name = name
            cs.description = (request.POST.get('description') or '').strip()
            cs.save()
            messages.success(request, 'Conjunto atualizado.')
        elif action == 'add_variation':
            text = (request.POST.get('text') or '').strip()
            if text:
                Caption.objects.create(
                    caption_set=cs,
                    text=text,
                    hashtags=(request.POST.get('hashtags') or '').strip(),
                )
                messages.success(request, 'Variação adicionada.')
        return redirect('library:edit_caption', caption_id=cs.id)

    return render(request, 'library/caption_edit.html', {
        'caption_set': cs,
        'captions': cs.captions.all(),
    })


@login_required
def delete_variation(request, variation_id):
    variation = get_object_or_404(Caption, id=variation_id, caption_set__owner=request.user)
    cs_id = variation.caption_set_id
    variation.delete()
    return redirect('library:edit_caption', caption_id=cs_id)

@login_required
def audios_list(request):
    audios = Audio.objects.filter(owner=request.user)
    return render(request, 'library/audios.html', {'audios': audios})

@login_required
def add_audio(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        file = request.FILES.get('file')
        if name and file:
            Audio.objects.create(owner=request.user, name=name, file=file)
    return redirect('library:audios')

@login_required
def delete_audio(request, audio_id):
    audio = get_object_or_404(Audio, id=audio_id, owner=request.user)
    audio.delete()
    return redirect('library:audios')


# =============================================================================
# Biblioteca de Mídia (vídeos/reels e capas, organizados em pastas)
# =============================================================================
@login_required
def media_list(request):
    folders = MediaFolder.objects.filter(owner=request.user)

    from django.core.paginator import Paginator

    current_folder_id = request.GET.get('folder')
    assets = MediaAsset.objects.filter(owner=request.user)
    current_folder = None
    if current_folder_id:
        current_folder = get_object_or_404(MediaFolder, id=current_folder_id, owner=request.user)
        assets = assets.filter(folder=current_folder)

    # Paginado: sem isso, "selecionar todas" com muitas mídias estoura o limite
    # de campos do Django (HTTP 400).
    paginator = Paginator(assets.order_by('-created_at'), 60)
    page = paginator.get_page(request.GET.get('page'))

    context = {
        'folders': folders,
        'assets': page,
        'page_obj': page,
        'total_filtrado': paginator.count,
        'current_folder': current_folder,
        'total_videos': MediaAsset.objects.filter(owner=request.user, kind='video').count(),
        'total_images': MediaAsset.objects.filter(owner=request.user, kind='image').count(),
    }
    return render(request, 'library/media.html', context)


@login_required
def add_folder(request):
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        if name:
            MediaFolder.objects.get_or_create(owner=request.user, name=name)
    return redirect('library:media')


@login_required
def delete_folder(request, folder_id):
    folder = get_object_or_404(MediaFolder, id=folder_id, owner=request.user)
    folder.delete()  # assets ficam com folder=NULL (SET_NULL)
    return redirect('library:media')


@login_required
def upload_media(request):
    if request.method == 'POST':
        folder = None
        folder_id = request.POST.get('folder')
        if folder_id:
            folder = MediaFolder.objects.filter(id=folder_id, owner=request.user).first()

        from apps.core_utils import nome_seguro

        files = request.FILES.getlist('files')
        count = 0
        for f in files:
            nome_original = f.name
            # Grava com nome ASCII: a Meta não consegue baixar URL com acento.
            f.name = nome_seguro(nome_original)
            MediaAsset.objects.create(
                owner=request.user,
                folder=folder,
                file=f,
                kind=MediaAsset.detect_kind(nome_original),
                original_name=nome_original[:255],
                size_bytes=getattr(f, 'size', 0) or 0,
            )
            count += 1
        if count:
            messages.success(request, f'{count} arquivo(s) enviado(s) para a biblioteca.')

    redirect_url = 'library:media'
    folder_id = request.POST.get('folder')
    if folder_id:
        return redirect(f"{ _media_url() }?folder={folder_id}")
    return redirect(redirect_url)


@login_required
@require_POST
def bulk_media(request):
    """Exclui várias mídias selecionadas de uma vez."""
    # "Selecionar todas" manda uma flag (+ pasta) em vez de um campo por mídia.
    if request.POST.get('todos') == '1':
        qs = MediaAsset.objects.filter(owner=request.user)
        pasta = (request.POST.get('folder') or '').strip()
        if pasta:
            qs = qs.filter(folder_id=pasta)
    else:
        qs = MediaAsset.objects.filter(id__in=request.POST.getlist('media_ids'),
                                       owner=request.user)
    n = qs.count()
    for a in qs:
        a.file.delete(save=False)
    qs.delete()
    messages.success(request, f'{n} mídia(s) excluída(s).')
    folder_id = (request.POST.get('folder') or '').strip()
    if folder_id:
        return redirect(f"{_media_url()}?folder={folder_id}")
    return redirect('library:media')


@login_required
def delete_media(request, asset_id):
    asset = get_object_or_404(MediaAsset, id=asset_id, owner=request.user)
    folder_id = asset.folder_id
    asset.delete()
    if folder_id:
        return redirect(f"{ _media_url() }?folder={folder_id}")
    return redirect('library:media')


def _media_url():
    from django.urls import reverse
    return reverse('library:media')


# =============================================================================
# Downloader — baixa o conteúdo de um perfil do Instagram e entrega em ZIP
# =============================================================================
import re as _re


def _extrair_username(valor):
    """Aceita URL do perfil (instagram.com/perfil/), @perfil ou só o nome, e
    devolve o username limpo."""
    v = (valor or '').strip()
    if not v:
        return ''
    # Se veio uma URL, pega o primeiro segmento do caminho.
    m = _re.search(r'instagram\.com/([^/?#]+)', v, _re.IGNORECASE)
    if m:
        v = m.group(1)
    v = v.lstrip('@').strip().strip('/')
    # Username do IG: letras, números, ponto e underline.
    v = _re.sub(r'[^A-Za-z0-9_.]', '', v)
    return v.lower()


@login_required
def downloader(request):
    from apps.instagram.models import InstagramAccount
    contas = [
        c for c in InstagramAccount.objects.filter(owner=request.user).order_by('ig_username')
        if c.tem_sessao_engine
    ]
    jobs = list(ProfileDownload.objects.filter(owner=request.user)[:30])
    return render(request, 'library/downloader.html', {
        'contas': contas,
        'jobs': jobs,
        'has_active': any(j.status in ('queued', 'running') for j in jobs),
    })


@login_required
@require_POST
def start_download(request):
    from apps.instagram.models import InstagramAccount

    username = _extrair_username(request.POST.get('profile', ''))
    if not username:
        messages.error(request, 'Informe a URL ou o @ do perfil.')
        return redirect('library:downloader')

    conta = InstagramAccount.objects.filter(
        id=request.POST.get('account'), owner=request.user
    ).first()
    if not conta or not conta.tem_sessao_engine:
        messages.error(request, 'Escolha uma conta conectada por sessão/senha para fazer a leitura.')
        return redirect('library:downloader')

    # Ao menos um tipo de conteúdo precisa estar marcado.
    quer_feed = request.POST.get('feed') == 'on'
    quer_reels = request.POST.get('reels') == 'on'
    quer_stories = request.POST.get('stories') == 'on'
    quer_highlights = request.POST.get('highlights') == 'on'
    if not any([quer_feed, quer_reels, quer_stories, quer_highlights]):
        messages.error(request, 'Marque pelo menos um tipo de conteúdo para baixar.')
        return redirect('library:downloader')

    try:
        amount = int(request.POST.get('amount') or 0)
    except ValueError:
        amount = 0
    amount = max(0, amount)

    job = ProfileDownload.objects.create(
        owner=request.user,
        account=conta,
        target_username=username,
        target_url=f'https://www.instagram.com/{username}/',
        want_feed=quer_feed,
        want_reels=quer_reels,
        want_stories=quer_stories,
        want_highlights=quer_highlights,
        amount=amount,
        progress_msg='Na fila…',
    )

    from .tasks import run_profile_download
    run_profile_download.delay(job.id)

    messages.success(request, f'Download de @{username} iniciado. Acompanhe o progresso abaixo.')
    return redirect('library:downloader')


@login_required
def downloads_status(request):
    """Só a lista de jobs — o HTMX recarrega isto sozinho para mostrar o progresso."""
    jobs = list(ProfileDownload.objects.filter(owner=request.user)[:30])
    return render(request, 'library/partials/_downloads.html', {
        'jobs': jobs,
        'has_active': any(j.status in ('queued', 'running') for j in jobs),
    })


@login_required
def delete_download(request, job_id):
    job = get_object_or_404(ProfileDownload, id=job_id, owner=request.user)
    if job.zip_file:
        job.zip_file.delete(save=False)
    job.delete()
    return redirect('library:downloader')


# =============================================================================
# Gerador de CTA — arte 9:16 com adesivo no estilo do Instagram
# =============================================================================
# Por que existe: para levar tráfego ao link, o story precisa de chamada visual.
# O adesivo NATIVO só existe quando a conta publica pela engine (sessão); pela
# API oficial não dá para anexar figurinha. Aqui a chamada é desenhada NA
# imagem, então funciona em qualquer conta — inclusive as só-token.
#
# A arte gerada entra na Biblioteca como imagem, então já pode ser postada como
# story/feed pelo Composer sem passo intermediário.

@login_required
def cta_generator(request):
    """Tela do gerador. O POST gera a arte e guarda na biblioteca."""
    import os
    import tempfile

    from engine.cta_render import TIPOS

    ultima = None
    if request.method == 'POST':
        ultima = _gerar_cta(request)
        if ultima:
            return redirect(f"{reverse('library:cta')}?ok={ultima.id}")

    ok_id = request.GET.get('ok')
    gerada = MediaAsset.objects.filter(id=ok_id, owner=request.user).first() if ok_id else None

    return render(request, 'library/cta.html', {
        'tipos': TIPOS,
        'gerada': gerada,
        'folders': MediaFolder.objects.filter(owner=request.user),
        # Só imagens: a arte é montada sobre uma foto.
        'imagens': MediaAsset.objects.filter(owner=request.user, kind='image')
                                     .order_by('-created_at')[:60],
    })


def _num(request, campo, padrao, minimo, maximo):
    try:
        v = float(request.POST.get(campo, padrao))
    except (TypeError, ValueError):
        v = padrao
    return max(minimo, min(v, maximo))


def _gerar_cta(request):
    """Monta a arte e devolve o MediaAsset criado (ou None se falhar)."""
    import os
    import tempfile

    from django.core.files.base import ContentFile

    from apps.core_utils import garantir_midia_local
    from engine.cta_render import gerar_cta

    # A imagem de fundo vem de um upload novo OU de uma imagem da biblioteca.
    base_local, base_temp = None, False
    enviada = request.FILES.get('imagem')
    escolhida_id = (request.POST.get('imagem_biblioteca') or '').strip()

    try:
        if enviada:
            fd, base_local = tempfile.mkstemp(
                suffix=os.path.splitext(enviada.name)[1] or '.jpg')
            with os.fdopen(fd, 'wb') as fh:
                for chunk in enviada.chunks():
                    fh.write(chunk)
            base_temp = True
        elif escolhida_id:
            asset = MediaAsset.objects.filter(
                id=escolhida_id, owner=request.user, kind='image').first()
            if asset:
                base_local, base_temp = garantir_midia_local(asset.file)

        destino_dir = os.path.join(settings.MEDIA_ROOT, 'cta')
        fd, destino = tempfile.mkstemp(suffix='.jpg', dir=_criar_dir(destino_dir))
        os.close(fd)

        gerar_cta(
            base_local,
            tipo=(request.POST.get('tipo') or 'link').strip(),
            titulo=(request.POST.get('titulo') or '').strip()[:200],
            titulo_cor=(request.POST.get('titulo_cor') or '#ffffff').strip()[:9],
            titulo_tamanho=int(_num(request, 'titulo_tamanho', 72, 24, 160)),
            titulo_y=_num(request, 'titulo_y', 0.16, 0.02, 0.95),
            sticker_texto=(request.POST.get('sticker_texto') or '').strip()[:80],
            opcao_a=(request.POST.get('opcao_a') or '').strip()[:40],
            opcao_b=(request.POST.get('opcao_b') or '').strip()[:40],
            sticker_y=_num(request, 'sticker_y', 0.62, 0.02, 0.95),
            sticker_escala=_num(request, 'sticker_escala', 1.0, 0.4, 2.0),
            escurecer=_num(request, 'escurecer', 0.25, 0.0, 0.85),
            destino=destino,
        )

        nome = (request.POST.get('nome') or 'cta').strip()[:80] or 'cta'
        with open(destino, 'rb') as fh:
            conteudo = fh.read()
        asset = MediaAsset(owner=request.user, kind='image',
                           original_name=f'{nome}.jpg',
                           size_bytes=len(conteudo))
        pasta_id = (request.POST.get('pasta') or '').strip()
        if pasta_id:
            asset.folder = MediaFolder.objects.filter(
                id=pasta_id, owner=request.user).first()
        asset.file.save(f'{nome}.jpg', ContentFile(conteudo), save=True)
        messages.success(request, 'Arte gerada e salva na Biblioteca.')
        return asset
    except Exception as e:
        messages.error(request, f'Não consegui gerar a arte: {e}')
        return None
    finally:
        for tmp in ((base_local if base_temp else None),):
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def _criar_dir(caminho):
    import os
    os.makedirs(caminho, exist_ok=True)
    return caminho
