import secrets

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.contrib.auth.models import AbstractUser
from cryptography.fernet import Fernet


def _get_fernet():
    """Fernet a partir da FERNET_KEY. Helper local para evitar import circular
    com apps.instagram.models (que já importa este módulo)."""
    key = (settings.FERNET_KEY or "").strip()
    try:
        return Fernet(key.encode())
    except Exception:
        raise ImproperlyConfigured(
            "FERNET_KEY ausente ou inválida. Defina a variável de ambiente FERNET_KEY."
        )


class User(AbstractUser):
    phone = models.CharField(max_length=20, blank=True)
    # Apelido opcional — usado nas saudações do painel.
    nickname = models.CharField(max_length=60, blank=True)
    plan_type = models.CharField(
        max_length=20,
        choices=[('free', 'Free'), ('pro', 'Pro'), ('agency', 'Agência')],
        default='free'
    )
    max_ig_accounts = models.IntegerField(default=3)
    avatar = models.ImageField(upload_to='avatars/', max_length=500, null=True, blank=True)
    # Token secreto usado pela extensão de navegador para autenticar o envio
    # do sessionid capturado do instagram.com (ver apps.instagram.views.connect_extension).
    extension_token = models.CharField(max_length=64, blank=True, db_index=True)
    # Credenciais do app Meta do PRÓPRIO usuário (cada um traz o seu app).
    # App IDs não são sigilosos; os secrets são guardados criptografados (Fernet).
    meta_app_id = models.CharField(max_length=64, blank=True)
    meta_app_secret_enc = models.TextField(blank=True)
    meta_login_config_id = models.CharField(max_length=64, blank=True)
    instagram_app_id = models.CharField(max_length=64, blank=True)
    instagram_app_secret_enc = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def set_meta_app_secret(self, raw_secret):
        """Criptografa e guarda o App Secret do Meta."""
        raw_secret = (raw_secret or '').strip()
        self.meta_app_secret_enc = (
            _get_fernet().encrypt(raw_secret.encode()).decode() if raw_secret else ''
        )

    def get_meta_app_secret(self):
        """App Secret em texto puro (ou '' se não configurado)."""
        stored = self.meta_app_secret_enc or ''
        if not stored:
            return ''
        try:
            return _get_fernet().decrypt(stored.encode()).decode()
        except Exception:
            return ''

    def set_instagram_app_secret(self, raw_secret):
        """Criptografa e guarda o App Secret do app do Instagram."""
        raw_secret = (raw_secret or '').strip()
        self.instagram_app_secret_enc = (
            _get_fernet().encrypt(raw_secret.encode()).decode() if raw_secret else ''
        )

    def get_instagram_app_secret(self):
        stored = self.instagram_app_secret_enc or ''
        if not stored:
            return ''
        try:
            return _get_fernet().decrypt(stored.encode()).decode()
        except Exception:
            return ''

    @property
    def has_meta_credentials(self):
        return bool((self.meta_app_id or '').strip()) and bool(self.meta_app_secret_enc)

    @property
    def display_name(self):
        """Nome curto para saudações: apelido > primeiro nome > usuário."""
        return (self.nickname or '').strip() or (self.first_name or '').strip() or self.username

    def get_active_meta_app(self):
        """App Meta em uso para novas conexões (ou None)."""
        return self.meta_apps.filter(is_active=True).first() or self.meta_apps.first()

    def ensure_extension_token(self):
        """Retorna o token da extensão, gerando um na primeira vez."""
        if not self.extension_token:
            self.extension_token = secrets.token_urlsafe(32)
            self.save(update_fields=['extension_token'])
        return self.extension_token

    def rotate_extension_token(self):
        self.extension_token = secrets.token_urlsafe(32)
        self.save(update_fields=['extension_token'])
        return self.extension_token


class MetaApp(models.Model):
    """Um app do Meta for Developers cadastrado pelo usuário.

    Permite manter VÁRIOS apps (ex.: um por cliente/projeto) e alternar qual
    é usado nas novas conexões. Os secrets são guardados criptografados.
    """
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='meta_apps')
    name = models.CharField(max_length=80)

    meta_app_id = models.CharField(max_length=64, blank=True)
    meta_app_secret_enc = models.TextField(blank=True)
    meta_login_config_id = models.CharField(max_length=64, blank=True)
    instagram_app_id = models.CharField(max_length=64, blank=True)
    instagram_app_secret_enc = models.TextField(blank=True)

    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_active', 'name']
        unique_together = ['owner', 'name']

    def __str__(self):
        return self.name

    # ── Secrets criptografados ──────────────────────────────────
    def set_meta_secret(self, raw):
        raw = (raw or '').strip()
        self.meta_app_secret_enc = _get_fernet().encrypt(raw.encode()).decode() if raw else ''

    def get_meta_secret(self):
        return _decrypt_or_empty(self.meta_app_secret_enc)

    def set_instagram_secret(self, raw):
        raw = (raw or '').strip()
        self.instagram_app_secret_enc = _get_fernet().encrypt(raw.encode()).decode() if raw else ''

    def get_instagram_secret(self):
        return _decrypt_or_empty(self.instagram_app_secret_enc)

    @property
    def is_complete(self):
        """Tem o mínimo para o fluxo OAuth (App ID + Secret)."""
        return bool((self.meta_app_id or '').strip()) and bool(self.meta_app_secret_enc)

    def activate(self):
        """Torna este o app ativo (desativa os outros do mesmo dono)."""
        MetaApp.objects.filter(owner=self.owner).update(is_active=False)
        self.is_active = True
        self.save(update_fields=['is_active'])


def _decrypt_or_empty(stored):
    stored = stored or ''
    if not stored:
        return ''
    try:
        return _get_fernet().decrypt(stored.encode()).decode()
    except Exception:
        return ''
