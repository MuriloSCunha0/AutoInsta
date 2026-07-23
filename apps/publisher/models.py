from django.db import models
from apps.accounts.models import User
from apps.instagram.models import InstagramAccount

class PostQueue(models.Model):
    """Fila nomeada. Uma conta pode ter VÁRIAS filas rodando em paralelo
    (ex.: 'Campanha A' e 'Promoções'), cada uma pausável de forma independente.

    O despacho continua sendo de 1 post por CONTA por rodada — as filas da mesma
    conta se revezam (round-robin), para não multiplicar o ritmo de publicação
    e cair no limite da Meta.
    """
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='filas')
    account = models.ForeignKey(InstagramAccount, on_delete=models.CASCADE, related_name='filas')
    name = models.CharField(max_length=80)
    paused = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # Marca a última vez que esta fila despachou (usado no rodízio).
    last_dispatch = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['name']
        unique_together = ['account', 'name']

    def __str__(self):
        return f"{self.name} (@{self.account.ig_username})"

    @property
    def pendentes(self):
        return self.posts.filter(status='queued').count()


class ScheduledPost(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Rascunho'),
        ('queued', 'Na fila'),
        ('processing', 'Publicando...'),
        ('published', 'Publicado ✅'),
        ('failed', 'Falhou ❌'),
    ]

    # A fila mostra só o que ainda dá trabalho; o que já publicou é histórico
    # e vive na tela de Publicados. São dados diferentes.
    STATUS_ATIVOS = ['draft', 'queued', 'processing', 'failed']

    TYPE_CHOICES = [
        ('REELS', 'Reels'),
        ('FEED', 'Feed'),
        ('STORY', 'Story'),
    ]

    account = models.ForeignKey(InstagramAccount, on_delete=models.CASCADE)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    # Fila a que este post pertence (opcional: sem fila = fila padrão da conta).
    queue = models.ForeignKey(
        'PostQueue', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='posts',
    )
    post_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default='REELS')
    # FileField tem max_length=100 por padrão — caminhos de mídia estouram isso
    # ("value too long for type character varying(100)").
    video_file = models.FileField(upload_to='reels/', max_length=500)
    thumbnail = models.FileField(upload_to='thumbnails/', max_length=500, null=True, blank=True)
    caption = models.TextField(blank=True)
    caption_set = models.ForeignKey('library.CaptionSet', on_delete=models.SET_NULL, null=True, blank=True)
    # Reel também na grade principal do perfil (parâmetro oficial share_to_feed).
    share_to_feed = models.BooleanField(default=True)
    # Confirmação vinda da Meta depois de publicar (campo is_shared_to_feed da
    # mídia). None = ainda não verificado. Serve para provar que a grade está
    # funcionando de verdade, em vez de confiar no que pedimos.
    na_grade = models.BooleanField(null=True, blank=True)
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

    # Trilha da aba Áudios: quando definida, substitui o áudio do vídeo.
    audio = models.ForeignKey(
        'library.Audio', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='posts',
    )
    
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
    """Ciclo automático de publicação.

    Dois modos:
      - PASTA  : rotaciona as mídias de uma pasta da biblioteca (recomendado)
      - ARQUIVO: republica sempre o mesmo arquivo (modo antigo)
    """
    account = models.ForeignKey(InstagramAccount, on_delete=models.CASCADE)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    post_type = models.CharField(max_length=10, choices=ScheduledPost.TYPE_CHOICES, default='REELS')

    # Modo pasta: gira os vídeos da pasta, um por ciclo.
    folder = models.ForeignKey(
        'library.MediaFolder', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='loops',
    )
    last_index = models.IntegerField(default=0)  # posição na rotação

    # Modo arquivo único (legado): republica sempre o mesmo vídeo.
    video_file = models.FileField(upload_to='loops/', max_length=500, blank=True)

    caption = models.TextField(blank=True)
    interval_minutes = models.IntegerField(default=1440)  # 1440 = 24h
    share_to_feed = models.BooleanField(default=True)
    clean_mode = models.CharField(max_length=10, choices=ScheduledPost.CLEAN_CHOICES, default='light')
    audio = models.ForeignKey(
        'library.Audio', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='loops',
    )

    last_posted = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        alvo = self.folder.name if self.folder else 'arquivo único'
        return f"@{self.account.ig_username} · {alvo}"

    @property
    def proxima_execucao(self):
        from datetime import timedelta
        if not self.last_posted:
            return None
        return self.last_posted + timedelta(minutes=self.interval_minutes)

    def midias_da_pasta(self):
        """Vídeos/imagens da pasta, em ordem estável."""
        if not self.folder:
            return []
        return list(self.folder.assets.order_by('id'))
