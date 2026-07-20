from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import ScheduledPost, PostLoop
from .forms import ScheduledPostForm, PostLoopForm
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
        color = '#a855f7' # purple for queued
        if post.status == 'published': color = '#22c55e' # green
        elif post.status == 'failed': color = '#ef4444' # red
        elif post.status == 'processing': color = '#f59e0b' # amber
        
        events.append({
            'id': post.id,
            'title': f"[{post.get_post_type_display()}] {post.account.ig_username}",
            'start': post.scheduled_for.isoformat(),
            'color': color,
            'url': f"/publisher/remove/{post.id}/" # Click to delete or edit
        })
        
    return JsonResponse(events, safe=False)
