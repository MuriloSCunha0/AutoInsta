from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import CaptionSet, Audio

@login_required
def captions_list(request):
    caption_sets = CaptionSet.objects.filter(owner=request.user)
    return render(request, 'library/captions.html', {'caption_sets': caption_sets})

@login_required
def audios_list(request):
    audios = Audio.objects.filter(owner=request.user)
    return render(request, 'library/audios.html', {'audios': audios})
