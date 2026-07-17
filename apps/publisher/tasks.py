from celery import shared_task
from django.utils import timezone
from .models import ScheduledPost
from engine.client import InstagramEngine

@shared_task
def process_scheduled_posts():
    """
    Tarefa periódica (Celery Beat) que busca posts agendados
    para agora ou para o passado que ainda estão na fila e os envia para processamento.
    """
    now = timezone.now()
    posts_to_publish = ScheduledPost.objects.filter(
        status='queued',
        scheduled_for__lte=now
    )
    
    for post in posts_to_publish:
        post.status = 'processing'
        post.save()
        publish_reel.delay(post.id)

@shared_task
def publish_reel(post_id):
    """
    Tarefa que faz o upload real do vídeo para o Instagram.
    """
    try:
        post = ScheduledPost.objects.get(id=post_id)
    except ScheduledPost.DoesNotExist:
        print(f"Post {post_id} não existe mais; ignorando.")
        return

    try:
        engine = InstagramEngine(post.account)

        # O caption final pode ser a mistura do texto e hashtags, etc
        final_caption = post.caption
        if post.caption_set:
            # Pegar uma legenda do set (aqui poderíamos usar a com menor used_count)
            caption_obj = post.caption_set.captions.order_by('used_count').first()
            if caption_obj:
                final_caption = f"{final_caption}\n\n{caption_obj.text}\n\n{caption_obj.hashtags}"
                caption_obj.used_count += 1
                caption_obj.save()

        video_path = post.video_file.path
        thumbnail_path = post.thumbnail.path if post.thumbnail else None
        
        media_info = engine.upload_reel(
            video_path=video_path,
            caption=final_caption,
            thumbnail_path=thumbnail_path
        )
        
        post.status = 'published'
        post.published_at = timezone.now()
        post.ig_media_id = str(media_info.get('id', ''))
        post.save()
        
    except Exception as e:
        if post.retry_count < post.max_retries:
            post.retry_count += 1
            post.status = 'queued'  # Voltar para fila
            post.error_message = str(e)
            post.save()
        else:
            post.status = 'failed'
            post.error_message = str(e)
            post.save()
        print(f"Error publishing post {post_id}: {str(e)}")
