from django.db import models
from apps.accounts.models import User
from apps.instagram.models import InstagramAccount

class ScheduledPost(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Rascunho'),
        ('queued', 'Na fila'),
        ('processing', 'Publicando...'),
        ('published', 'Publicado ✅'),
        ('failed', 'Falhou ❌'),
    ]

    TYPE_CHOICES = [
        ('REELS', 'Reels'),
        ('FEED', 'Feed'),
        ('STORY', 'Story'),
    ]

    account = models.ForeignKey(InstagramAccount, on_delete=models.CASCADE)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    post_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default='REELS')
    # FileField tem max_length=100 por padrão — caminhos de mídia estouram isso
    # ("value too long for type character varying(100)").
    video_file = models.FileField(upload_to='reels/', max_length=500)
    thumbnail = models.FileField(upload_to='thumbnails/', max_length=500, null=True, blank=True)
    caption = models.TextField(blank=True)
    caption_set = models.ForeignKey('library.CaptionSet', on_delete=models.SET_NULL, null=True, blank=True)
    # Reel também na grade principal do perfil (parâmetro oficial share_to_feed).
    share_to_feed = models.BooleanField(default=True)
    # Link do Story. A API oficial não permite sticker de link, então quando
    # preenchido a publicação vai pela engine (instagrapi).
    story_link = models.URLField(max_length=500, blank=True)

    # Limpeza/diversificação do arquivo antes de publicar, para o Instagram
    # não correlacionar contas que enviam a mesma mídia.
    CLEAN_CHOICES = [
        ('none', 'Sem limpeza'),
        ('light', 'Limpeza leve'),
        ('ultra', 'Ultra clean'),
    ]
    clean_mode = models.CharField(max_length=10, choices=CLEAN_CHOICES, default='light')
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued')
    scheduled_for = models.DateTimeField()
    published_at = models.DateTimeField(null=True, blank=True)
    ig_media_id = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    retry_count = models.IntegerField(default=0)
    max_retries = models.IntegerField(default=3)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['scheduled_for', 'created_at']

class PostLoop(models.Model):
    account = models.ForeignKey(InstagramAccount, on_delete=models.CASCADE)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    post_type = models.CharField(max_length=10, choices=ScheduledPost.TYPE_CHOICES, default='REELS')
    video_file = models.FileField(upload_to='loops/', max_length=500)
    caption = models.TextField(blank=True)
    interval_days = models.IntegerField(default=7) # Re-post every X days
    last_posted = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
