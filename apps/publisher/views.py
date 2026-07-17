from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import ScheduledPost
from .forms import ScheduledPostForm

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
