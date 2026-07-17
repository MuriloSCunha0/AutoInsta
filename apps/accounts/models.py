import secrets

from django.db import models
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    phone = models.CharField(max_length=20, blank=True)
    plan_type = models.CharField(
        max_length=20,
        choices=[('free', 'Free'), ('pro', 'Pro'), ('agency', 'Agência')],
        default='free'
    )
    max_ig_accounts = models.IntegerField(default=3)
    # Token secreto usado pela extensão de navegador para autenticar o envio
    # do sessionid capturado do instagram.com (ver apps.instagram.views.connect_extension).
    extension_token = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

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
