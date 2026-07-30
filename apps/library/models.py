from django.db import models
from apps.accounts.models import User

class CaptionSet(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Caption(models.Model):
    caption_set = models.ForeignKey(CaptionSet, on_delete=models.CASCADE, related_name='captions')
    text = models.TextField()
    hashtags = models.TextField(blank=True)
    used_count = models.IntegerField(default=0)

class Audio(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    file = models.FileField(upload_to='audios/', max_length=500)
    duration_seconds = models.FloatField(null=True, blank=True)
    used_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class MediaFolder(models.Model):
    """Pasta para organizar mídias (reels/capas). Cada loop pode apontar para uma."""
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='media_folders')
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        unique_together = ['owner', 'name']

    def __str__(self):
        return self.name


class MediaAsset(models.Model):
    """Vídeo (reel) ou imagem (capa) na biblioteca de mídia do usuário."""
    KIND_CHOICES = [
        ('video', 'Vídeo'),
        ('image', 'Imagem/Capa'),
    ]
    VIDEO_EXTS = ('.mp4', '.mov', '.m4v', '.webm')

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='media_assets')
    folder = models.ForeignKey(MediaFolder, on_delete=models.SET_NULL, null=True, blank=True, related_name='assets')
    file = models.FileField(upload_to='media_library/', max_length=500)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default='video')
    original_name = models.CharField(max_length=255, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    used_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @staticmethod
    def detect_kind(filename):
        name = (filename or '').lower()
        return 'video' if name.endswith(MediaAsset.VIDEO_EXTS) else 'image'

    def __str__(self):
        return self.original_name or self.file.name


class ProfileDownload(models.Model):
    """Downloader: baixa o conteúdo de um perfil do Instagram e empacota num ZIP.

    Serve para manter um banco de conteúdo constante — pega feed, reels, stories
    atuais e destaques de um perfil (público, ou que a conta usada já siga) via
    instagrapi (a API oficial não lê perfil de terceiros). Roda numa task Celery
    porque perfil inteiro demora; a tela acompanha o progresso por HTMX.
    """
    STATUS_CHOICES = [
        ('queued', 'Na fila'),
        ('running', 'Baixando...'),
        ('done', 'Pronto ✅'),
        ('failed', 'Falhou ❌'),
    ]

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='downloads')
    # Conta que faz a LEITURA do perfil-alvo (sessão instagrapi). Se a conta for
    # removida, o job histórico continua existindo.
    account = models.ForeignKey(
        'instagram.InstagramAccount', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='downloads',
    )
    target_username = models.CharField(max_length=150)
    target_url = models.CharField(max_length=300, blank=True)

    # O que baixar (o usuário marca no formulário).
    want_feed = models.BooleanField(default=True)
    want_reels = models.BooleanField(default=True)
    want_stories = models.BooleanField(default=True)
    want_highlights = models.BooleanField(default=True)
    # Teto de posts do feed/reels. 0 = perfil inteiro.
    amount = models.IntegerField(default=0)

    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='queued')
    progress_msg = models.CharField(max_length=255, blank=True)
    media_count = models.IntegerField(default=0)   # itens já baixados
    zip_file = models.FileField(upload_to='downloads/', max_length=500, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"@{self.target_username} ({self.get_status_display()})"

    @property
    def size_mb(self):
        return round((self.size_bytes or 0) / (1024 * 1024), 1)
