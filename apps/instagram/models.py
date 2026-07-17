from django.db import models
from apps.accounts.models import User
from cryptography.fernet import Fernet
from django.conf import settings

class InstagramAccount(models.Model):
    STATUS_CHOICES = [
        ('connecting', 'Conectando...'),
        ('active', 'Ativa ✅'),
        ('challenge_required', 'Código necessário 🔑'),
        ('2fa_required', '2FA necessário 🔐'),
        ('session_expired', 'Sessão expirada 🕒'),
        ('error', 'Erro ❌'),
    ]

    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    ig_username = models.CharField(max_length=150)
    ig_password = models.TextField()
    proxy_url = models.CharField(max_length=255, blank=True, help_text="Ex: http://user:pass@ip:port")
    ig_user_id = models.BigIntegerField(null=True, blank=True)
    profile_pic_url = models.URLField(blank=True)
    full_name = models.CharField(max_length=200, blank=True)
    bio = models.TextField(blank=True)
    followers_count = models.IntegerField(default=0)
    following_count = models.IntegerField(default=0)
    posts_count = models.IntegerField(default=0)

    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='connecting')
    session_blob = models.JSONField(null=True, blank=True)
    device_settings = models.JSONField(null=True, blank=True)
    challenge_type = models.CharField(max_length=50, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    last_action_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['owner', 'ig_username']

    def set_ig_password(self, raw_password):
        f = Fernet(settings.FERNET_KEY.encode())
        self.ig_password = f.encrypt(raw_password.encode()).decode()

    def get_ig_password(self):
        f = Fernet(settings.FERNET_KEY.encode())
        return f.decrypt(self.ig_password.encode()).decode()

    @property
    def is_active(self):
        return self.status == 'active'
