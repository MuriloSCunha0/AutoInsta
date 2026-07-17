from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import CaptionSet, Audio

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
