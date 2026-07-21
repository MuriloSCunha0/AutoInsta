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

        # Spintax: Processar variáveis dinâmicas na legenda
        hoje = timezone.now()
        dias_semana = ['Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira', 'Sábado', 'Domingo']
        
        spintax_map = {
            '{nome_conta}': post.account.ig_username,
            '{dia_semana}': dias_semana[hoje.weekday()],
            '{data_hoje}': hoje.strftime('%d/%m/%Y'),
        }
        
        for key, value in spintax_map.items():
            final_caption = final_caption.replace(key, value)

        # Lógica de Roteamento: API Oficial (Meta) vs Automação (Session/Selenium)
        if post.account.meta_access_token:
            from django.conf import settings
            # Exige que SITE_URL esteja configurado no .env (ex: https://meusite.com)
            site_url = getattr(settings, 'SITE_URL', 'http://localhost:8000').rstrip('/')
            
            video_url = f"{site_url}{post.video_file.url}"
            cover_url = f"{site_url}{post.thumbnail.url}" if post.thumbnail else None
            
            print(f"Publicando {post.id} via Meta Graph API Oficial...")
            media_info = engine.upload_reel_meta_api(
                video_url=video_url,
                caption=final_caption,
                cover_url=cover_url,
                share_to_feed=post.share_to_feed,
            )
            post.ig_media_id = str(media_info.get('id', ''))
            
        else:
            print(f"Publicando {post.id} via Automação (Session)...")
            video_path = post.video_file.path
            thumbnail_path = post.thumbnail.path if post.thumbnail else None
            
            media_info = engine.upload_reel(
                video_path=video_path,
                caption=final_caption,
                thumbnail_path=thumbnail_path
            )
            post.ig_media_id = str(media_info.get('id', ''))
        
        post.status = 'published'
        post.published_at = timezone.now()
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
