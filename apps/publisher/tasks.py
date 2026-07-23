from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from .models import ScheduledPost, PostLoop
from engine.client import InstagramEngine


@shared_task
def process_loops():
    """Enfileira a próxima publicação de cada Loop ativo cujo intervalo venceu.

    No modo PASTA, gira as mídias em ciclo (uma por vez) usando last_index —
    assim o loop nunca repete a mesma mídia em sequência enquanto houver
    outras na pasta.
    """
    agora = timezone.now()

    for loop in PostLoop.objects.filter(is_active=True).select_related('account', 'folder', 'owner'):
        # Fila do usuário pausada? não enfileira novos.
        if loop.owner.publishing_paused:
            continue
        # Ainda não venceu o intervalo?
        if loop.last_posted and (agora - loop.last_posted) < timedelta(minutes=loop.interval_minutes):
            continue

        nome_arquivo = None

        if loop.folder:
            midias = loop.midias_da_pasta()
            if not midias:
                continue  # pasta vazia: nada a fazer
            indice = loop.last_index % len(midias)
            asset = midias[indice]
            nome_arquivo = asset.file.name
            loop.last_index = (indice + 1) % len(midias)
            asset.used_count += 1
            asset.save(update_fields=['used_count'])
        elif loop.video_file:
            nome_arquivo = loop.video_file.name

        if not nome_arquivo:
            continue

        post = ScheduledPost(
            owner=loop.owner,
            account=loop.account,
            post_type=loop.post_type,
            caption=loop.caption,
            share_to_feed=loop.share_to_feed,
            clean_mode=loop.clean_mode,
            audio=loop.audio,
            status='queued',
            scheduled_for=agora,
        )
        post.video_file.name = nome_arquivo
        post.save()

        loop.last_posted = agora
        loop.save(update_fields=['last_posted', 'last_index'])
        print(f"Loop {loop.id}: enfileirou post {post.id} (@{loop.account.ig_username})")

@shared_task
def process_scheduled_posts():
    """
    Tarefa periódica (Celery Beat) que despacha os posts vencidos — de forma
    CONTROLADA, para não martelar a API da Meta (o que dispara bloqueios):

      - no máximo 1 post por CONTA por rodada (espaça as publicações);
      - pula contas em cooldown de rate limit;
      - respeita o limite diário da conta (reagenda o excedente).
    """
    now = timezone.now()
    janela_24h = now - timedelta(hours=24)

    from django.db.models import F

    due = (ScheduledPost.objects.filter(status='queued', scheduled_for__lte=now)
           .select_related('account', 'owner', 'queue')
           # Rodízio: a fila que despachou há mais tempo vem primeiro, então
           # várias filas da mesma conta avançam de forma justa.
           # nulls_first é ESSENCIAL: no PostgreSQL, ASC joga NULL para o fim,
           # e a fila que nunca despachou (NULL) ficaria sempre por último —
           # o que fazia uma fila drenar inteira antes da outra começar.
           .order_by(F('queue__last_dispatch').asc(nulls_first=True), 'scheduled_for'))

    despachadas = set()
    for post in due:
        conta = post.account

        # Fila pausada pelo usuário: não publica nada dele.
        if post.owner.publishing_paused:
            continue

        # Fila nomeada pausada individualmente.
        if post.queue and post.queue.paused:
            continue

        if conta.id in despachadas:
            continue  # já mandamos um post desta conta nesta rodada

        # Conta em cooldown por rate limit: não toca.
        if conta.rate_limited_until and conta.rate_limited_until > now:
            continue

        # Respeita o teto diário (0 = sem limite).
        limite = conta.daily_post_limit or 0
        if limite > 0:
            publicados_24h = ScheduledPost.objects.filter(
                account=conta, status='published', published_at__gte=janela_24h
            ).count()
            if publicados_24h >= limite:
                post.scheduled_for = now + timedelta(hours=2)
                post.save(update_fields=['scheduled_for'])
                continue

        post.status = 'processing'
        post.save(update_fields=['status'])
        publish_reel.delay(post.id)
        despachadas.add(conta.id)

        # Marca o rodízio: esta fila acabou de despachar.
        if post.queue:
            post.queue.last_dispatch = now
            post.queue.save(update_fields=['last_dispatch'])


def _e_rate_limit(msg):
    """A mensagem de erro indica limite de publicações da Meta?"""
    m = (msg or '').lower()
    return (
        'too many actions' in m
        or '2207042' in m
        or "'code': 9" in m
        or 'número máximo' in m
        or 'numero maximo' in m
        or 'application request limit' in m
        or 'rate limit' in m
    )

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

        # Detecta imagem x vídeo pela extensão do arquivo.
        IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.webp')
        is_image = (post.video_file.name or '').lower().endswith(IMAGE_EXTS)

        # ── Limpeza / diversificação do arquivo ────────────────────────────
        # Cada conta publica um arquivo com hash (e, no ultra, fingerprint)
        # diferente, para o Instagram não correlacionar as contas.
        import os
        from django.conf import settings as dj_settings

        publish_path = post.video_file.path
        publish_relname = post.video_file.name
        arquivo_temporario = None
        temp_audio = None

        # ── Trilha da aba Áudios (substitui o som do vídeo) ────────────────
        # Feito ANTES da limpeza, para o fingerprint valer sobre o arquivo final.
        if post.audio_id and not is_image:
            from engine.media_cleaner import aplicar_audio
            com_audio = aplicar_audio(
                publish_path,
                post.audio.file.path,
                dest_dir=os.path.join(dj_settings.MEDIA_ROOT, 'processed'),
            )
            if com_audio and com_audio != publish_path:
                publish_path = com_audio
                temp_audio = com_audio
                publish_relname = os.path.relpath(com_audio, dj_settings.MEDIA_ROOT).replace('\\', '/')
                post.audio.used_count += 1
                post.audio.save(update_fields=['used_count'])
                print(f"Post {post.id}: trilha '{post.audio.name}' aplicada")

        clean_mode = getattr(post, 'clean_mode', 'none') or 'none'
        if clean_mode != 'none' and not is_image:
            from engine.media_cleaner import limpar_video
            processado = limpar_video(
                publish_path,
                mode=clean_mode,
                # Seed por conta+mídia: mesma conta gera sempre o mesmo
                # tratamento, contas diferentes geram arquivos diferentes.
                seed=f"{post.account_id}-{post.video_file.name}",
                dest_dir=os.path.join(dj_settings.MEDIA_ROOT, 'processed'),
            )
            if processado and processado != publish_path:
                publish_path = processado
                arquivo_temporario = processado
                publish_relname = os.path.relpath(
                    processado, dj_settings.MEDIA_ROOT
                ).replace('\\', '/')
                print(f"Post {post.id}: mídia processada (modo={clean_mode})")

        # Story COM LINK só é possível pela engine (a API oficial não expõe
        # sticker de link). Se houver link, usamos o caminho da engine.
        story_link = (getattr(post, 'story_link', '') or '').strip()

        if post.post_type == 'STORY' and story_link:
            print(f"Publicando Story com link {post.id} via engine...")
            media_info = engine.upload_story(publish_path, link_url=story_link)
            post.ig_media_id = str(media_info.get('pk') or media_info.get('id') or '')

        elif post.account.meta_access_token:
            from django.conf import settings
            # SITE_URL precisa ser pública: a Meta baixa a mídia dessa URL.
            site_url = getattr(settings, 'SITE_URL', 'http://localhost:8000').rstrip('/')

            media_url = f"{site_url}{dj_settings.MEDIA_URL}{publish_relname}"
            cover_url = f"{site_url}{post.thumbnail.url}" if post.thumbnail else None

            print(f"Publicando {post.id} ({post.post_type}) via Meta Graph API Oficial...")
            media_info = engine.publish_meta_api(
                media_url=media_url,
                caption=final_caption,
                post_type=post.post_type,
                cover_url=cover_url,
                share_to_feed=post.share_to_feed,
                is_image=is_image,
            )
            post.ig_media_id = str(media_info.get('id', ''))

        else:
            print(f"Publicando {post.id} via Automação (Session)...")
            if post.post_type == 'STORY':
                media_info = engine.upload_story(publish_path, link_url=story_link or None)
                post.ig_media_id = str(media_info.get('pk') or media_info.get('id') or '')
            else:
                media_info = engine.upload_reel(
                    video_path=publish_path,
                    caption=final_caption,
                    thumbnail_path=post.thumbnail.path if post.thumbnail else None,
                )
                post.ig_media_id = str(media_info.get('id', ''))

        post.status = 'published'
        post.published_at = timezone.now()
        post.save()

        # Publicou: a conta claramente não está mais em cooldown.
        if post.account.rate_limited_until:
            post.account.rate_limited_until = None
            post.account.save(update_fields=['rate_limited_until'])

        # Remove as cópias temporárias: já publicadas, não precisam ocupar disco.
        for temporario in (arquivo_temporario, temp_audio):
            if temporario:
                try:
                    os.remove(temporario)
                except Exception:
                    pass
        
    except Exception as e:
        msg = str(e)

        if _e_rate_limit(msg):
            # Rate limit da Meta: NÃO conta como retry (a conta simplesmente
            # atingiu o teto de 24h). Coloca a conta em cooldown e reagenda o
            # post — assim paramos de martelar a API imediatamente.
            cooldown = timezone.now() + timedelta(hours=3)
            post.account.rate_limited_until = cooldown
            post.account.save(update_fields=['rate_limited_until'])
            post.status = 'queued'
            post.scheduled_for = cooldown
            post.error_message = 'Limite de publicações da Meta atingido — reagendado após cooldown.'
            post.save()
            print(f"Post {post_id}: rate limit; @{post.account.ig_username} em cooldown até {cooldown}")

        elif post.retry_count < post.max_retries:
            # Erro transitório: espera antes de tentar de novo (não no mesmo minuto).
            post.retry_count += 1
            post.status = 'queued'
            post.scheduled_for = timezone.now() + timedelta(minutes=10)
            post.error_message = msg
            post.save()
            print(f"Error publishing post {post_id} (retry {post.retry_count}): {msg[:200]}")

        else:
            post.status = 'failed'
            post.error_message = msg
            post.save()
            print(f"Post {post_id} FALHOU em definitivo: {msg[:200]}")
