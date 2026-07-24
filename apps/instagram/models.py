from django.db import models
from apps.accounts.models import User
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _get_fernet():
    """Retorna um Fernet válido ou levanta um erro claro de configuração."""
    key = (settings.FERNET_KEY or "").strip()
    try:
        return Fernet(key.encode())
    except Exception:
        raise ImproperlyConfigured(
            "FERNET_KEY ausente ou inválida. Gere uma com "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` e defina a variável "
            "de ambiente FERNET_KEY no Railway."
        )

class InstagramAccount(models.Model):
    STATUS_CHOICES = [
        ('connecting', 'Conectando...'),
        ('active', 'Ativa ✅'),
        ('challenge_required', 'Código necessário 🔑'),
        ('2fa_required', '2FA necessário 🔐'),
        ('session_expired', 'Sessão expirada 🕒'),
        ('banned', 'Banida/indisponível 🚫'),
        ('error', 'Erro ❌'),
    ]

    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    # App Meta pelo qual esta conta foi conectada. Cada conta pertence ao app
    # que gerou seu token — por isso o vínculo é por conta, não global.
    meta_app = models.ForeignKey(
        'accounts.MetaApp', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='accounts',
    )
    ig_username = models.CharField(max_length=150)
    ig_password = models.TextField()
    proxy_url = models.CharField(max_length=255, blank=True, help_text="Ex: http://user:pass@ip:port")
    ig_user_id = models.BigIntegerField(null=True, blank=True)
    # URLs de foto do CDN do Instagram passam de 300-800 chars; o default do
    # URLField (200) estourava com "value too long for type character varying(200)".
    profile_pic_url = models.URLField(max_length=1000, blank=True)
    full_name = models.CharField(max_length=255, blank=True)
    bio = models.TextField(blank=True)
    followers_count = models.IntegerField(default=0)
    following_count = models.IntegerField(default=0)
    posts_count = models.IntegerField(default=0)

    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='connecting')
    # Teto de publicações por dia nesta conta (0 = sem limite). Ajuda a evitar
    # bloqueios por volume — o Composer distribui o excedente para os dias
    # seguintes quando o modo "Respeitar limite" está ligado.
    daily_post_limit = models.IntegerField(default=20)
    # Quando a Meta sinaliza rate limit, a conta fica em espera até este horário.
    # Enquanto isso, a fila NÃO tenta publicar nela (evita martelar a API — o
    # que é o padrão que dispara bans).
    rate_limited_until = models.DateTimeField(null=True, blank=True)
    # Modo forçado: ignora o teto diário e o cooldown de rate limit desta conta.
    # É o usuário assumindo o risco — a Meta ainda pode recusar por volume real.
    ignorar_limites = models.BooleanField(default=False)
    # Cota real de publicação da Meta (endpoint content_publishing_limit),
    # janela móvel de 24h. Preenchida na sincronização.
    quota_usage = models.IntegerField(default=0)
    quota_total = models.IntegerField(default=0)
    quota_checked_at = models.DateTimeField(null=True, blank=True)
    # Visualizações reais da Meta (endpoint /insights, métrica `views`).
    # `views_today` = dia corrente; `views_total` = tudo que a Meta ainda
    # guarda (ela mantém no máximo 2 anos de insights).
    views_today = models.IntegerField(default=0)
    views_total = models.IntegerField(default=0)
    views_checked_at = models.DateTimeField(null=True, blank=True)
    # Moderação: banimento manual pelo admin (independe do status da Meta).
    # Quando True, a conta não publica mais — usado quando o admin revisa o
    # conteúdo e decide barrar. Silencioso: o usuário não é notificado.
    banned_by_admin = models.BooleanField(default=False)
    banned_reason = models.CharField(max_length=255, blank=True)
    banned_at = models.DateTimeField(null=True, blank=True)
    session_blob = models.JSONField(null=True, blank=True)
    meta_access_token = models.TextField(blank=True, help_text="Token da API Oficial (Meta Graph)")
    device_settings = models.JSONField(null=True, blank=True)
    challenge_type = models.CharField(max_length=50, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    last_action_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['owner', 'ig_username']

    def __str__(self):
        """Exibido nos selects de formulário (antes saía 'InstagramAccount object (12)')."""
        name = f"@{self.ig_username}" if self.ig_username else f"conta #{self.pk}"
        return f"{name} — {self.full_name}" if self.full_name else name

    def set_ig_password(self, raw_password):
        self.ig_password = _get_fernet().encrypt(raw_password.encode()).decode()

    def get_ig_password(self):
        return _get_fernet().decrypt(self.ig_password.encode()).decode()

    def set_meta_token(self, raw_token):
        """Criptografa e guarda o token da Meta Graph API (mesmo cofre da senha)."""
        self.meta_access_token = (
            _get_fernet().encrypt(raw_token.encode()).decode() if raw_token else ''
        )

    def get_meta_token(self):
        """Token Meta em texto puro. Tolera tokens legados salvos sem criptografia."""
        stored = self.meta_access_token or ''
        if not stored:
            return ''
        try:
            return _get_fernet().decrypt(stored.encode()).decode()
        except Exception:
            # Token gravado antes da criptografia: devolve como está (retrocompat).
            return stored

    @property
    def is_active(self):
        return self.status == 'active'

    @property
    def em_cooldown(self):
        """Conta em espera por rate limit da Meta neste momento."""
        from django.utils import timezone
        return bool(self.rate_limited_until and self.rate_limited_until > timezone.now())

    @property
    def esta_limitada(self):
        """Está barrada agora — por cooldown da Meta ou pelo teto diário."""
        from datetime import timedelta

        from django.utils import timezone

        if self.em_cooldown:
            return True
        limite = self.daily_post_limit or 0
        if limite <= 0:
            return False
        from apps.publisher.models import ScheduledPost
        publicados = ScheduledPost.objects.filter(
            account=self, status='published',
            published_at__gte=timezone.now() - timedelta(hours=24),
        ).count()
        return publicados >= limite

    @property
    def tem_sessao_engine(self):
        """A engine (instagrapi) precisa de sessão salva ou senha utilizável.

        Contas conectadas SÓ por token da Meta não têm isso — e, por isso,
        não conseguem usar recursos que a API oficial não expõe (aquecimento,
        edição de bio/foto, Story com link).
        """
        if self.session_blob:
            return True
        try:
            return self.get_ig_password() not in ('', '__session_login__')
        except Exception:
            return False

class WarmupConfig(models.Model):
    """Configuração de aquecimento (warm-up) por conta — ações sociais graduais
    para maturar contas novas. Só possível pela engine cinza (a API oficial não
    expõe likes/follows/views), o que é um diferencial sobre soluções API-only."""
    INTENSITY_CHOICES = [
        ('low', 'Leve'),
        ('medium', 'Moderado'),
        ('high', 'Agressivo'),
    ]
    # Alvos diários por intensidade: (likes, follows, views)
    INTENSITY_TARGETS = {
        'low': (10, 2, 20),
        'medium': (25, 5, 50),
        'high': (50, 10, 100),
    }

    account = models.OneToOneField(InstagramAccount, on_delete=models.CASCADE, related_name='warmup')
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False)
    intensity = models.CharField(max_length=10, choices=INTENSITY_CHOICES, default='low')
    target_hashtag = models.CharField(max_length=100, default='reels')

    # Contadores do dia (resetam quando counter_date muda)
    counter_date = models.DateField(null=True, blank=True)
    likes_today = models.IntegerField(default=0)
    follows_today = models.IntegerField(default=0)
    views_today = models.IntegerField(default=0)

    last_run = models.DateTimeField(null=True, blank=True)
    last_result = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def daily_targets(self):
        return self.INTENSITY_TARGETS.get(self.intensity, self.INTENSITY_TARGETS['low'])


class Proxy(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    ip_address = models.CharField(max_length=50)
    port = models.IntegerField()
    username = models.CharField(max_length=150, blank=True)
    password = models.CharField(max_length=150, blank=True)
    protocol = models.CharField(max_length=20, default='http')
    is_active = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.protocol}://{self.ip_address}:{self.port}"
        
    @property
    def url(self):
        if self.username and self.password:
            return f"{self.protocol}://{self.username}:{self.password}@{self.ip_address}:{self.port}"
        return f"{self.protocol}://{self.ip_address}:{self.port}"
