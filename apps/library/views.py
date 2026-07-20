from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.conf import settings
from .models import CaptionSet, Caption, Audio, MediaFolder, MediaAsset

@login_required
def captions_list(request):
    caption_sets = CaptionSet.objects.filter(owner=request.user)
    return render(request, 'library/captions.html', {'caption_sets': caption_sets})

@login_required
def add_caption(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        if name:
            CaptionSet.objects.create(owner=request.user, name=name, description=description)
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
@require_POST
def generate_caption_ai(request):
    """Gera uma legenda com IA (OpenAI). Diferencial sobre templates estáticos."""
    import requests

    api_key = getattr(settings, 'OPENAI_API_KEY', '')
    if not api_key:
        return JsonResponse({'ok': False, 'error': 'OPENAI_API_KEY não configurada no servidor.'}, status=400)

    prompt = (request.POST.get('prompt') or '').strip()
    tone = (request.POST.get('tone') or 'envolvente').strip()
    if not prompt:
        return JsonResponse({'ok': False, 'error': 'Descreva o tema da legenda.'}, status=400)

    system = (
        "Você é um especialista em copywriting para Instagram. Gere UMA legenda curta, "
        "envolvente, em português do Brasil, com emojis moderados e 5 a 8 hashtags relevantes "
        "ao final. Pode usar a variável {nome_conta} se fizer sentido. Não use aspas ao redor do texto."
    )
    try:
        resp = requests.post(
            'https://api.openai.com/v1/chat/completions',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={
                'model': getattr(settings, 'OPENAI_MODEL', 'gpt-4o-mini'),
                'messages': [
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': f'Tema: {prompt}\nTom desejado: {tone}'},
                ],
                'temperature': 0.9,
                'max_tokens': 400,
            },
            timeout=30,
        )
        data = resp.json()
        if 'choices' not in data:
            return JsonResponse({'ok': False, 'error': data.get('error', {}).get('message', 'Falha na API OpenAI.')}, status=502)
        text = data['choices'][0]['message']['content'].strip()
        return JsonResponse({'ok': True, 'text': text})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=500)

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

    current_folder_id = request.GET.get('folder')
    assets = MediaAsset.objects.filter(owner=request.user)
    current_folder = None
    if current_folder_id:
        current_folder = get_object_or_404(MediaFolder, id=current_folder_id, owner=request.user)
        assets = assets.filter(folder=current_folder)

    context = {
        'folders': folders,
        'assets': assets,
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

        files = request.FILES.getlist('files')
        count = 0
        for f in files:
            MediaAsset.objects.create(
                owner=request.user,
                folder=folder,
                file=f,
                kind=MediaAsset.detect_kind(f.name),
                original_name=f.name[:255],
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
