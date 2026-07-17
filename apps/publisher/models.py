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

    account = models.ForeignKey(InstagramAccount, on_delete=models.CASCADE)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    video_file = models.FileField(upload_to='reels/')
    thumbnail = models.FileField(upload_to='thumbnails/', null=True, blank=True)
    caption = models.TextField(blank=True)
    caption_set = models.ForeignKey('library.CaptionSet', on_delete=models.SET_NULL, null=True, blank=True)
    
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
