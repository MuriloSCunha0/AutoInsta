from django.db import models
from apps.accounts.models import User
from apps.instagram.models import InstagramAccount


class AlertPreference(models.Model):
    """O que o usuário quer ser avisado, e por onde.

    O alerta sempre aparece no sino do painel; o Telegram é opcional e é o
    canal que chega no celular (como no Murphy).
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='alertas')

    # O que avisar
    conta_caiu = models.BooleanField(default=True)          # token revogado/expirado/banida
    falha_publicacao = models.BooleanField(default=True)     # post falhou em definitivo
    limite_atingido = models.BooleanField(default=True)      # conta bateu o teto/cooldown
    meta_views = models.BooleanField(default=True)           # bateu a meta de views do dia
    meta_views_alvo = models.IntegerField(default=10000)
    resumo_diario = models.BooleanField(default=False)       # resumo do dia

    # Canal no celular (opcional)
    telegram_chat_id = models.CharField(max_length=64, blank=True)
    telegram_token_enc = models.TextField(blank=True)

    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Alertas de {self.user.username}'

    def set_telegram_token(self, raw):
        from apps.accounts.models import _get_fernet
        raw = (raw or '').strip()
        self.telegram_token_enc = (
            _get_fernet().encrypt(raw.encode()).decode() if raw else ''
        )

    def get_telegram_token(self):
        """Token do bot do usuário; se não houver, o do sistema (.env)."""
        from django.conf import settings

        from apps.accounts.models import _get_fernet
        if self.telegram_token_enc:
            try:
                return _get_fernet().decrypt(self.telegram_token_enc.encode()).decode()
            except Exception:
                return ''
        return getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or ''

    @property
    def telegram_ativo(self):
        return bool(self.telegram_chat_id and self.get_telegram_token())


class PushSubscription(models.Model):
    """Aparelho inscrito para receber notificações Web Push (PWA).

    Cada navegador/dispositivo do usuário vira uma inscrição. É o que faz a
    notificação chegar no celular mesmo com o site fechado — sem bot, só o
    próprio site instalado como app.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='push_subs')
    endpoint = models.TextField(unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    user_agent = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def como_dict(self):
        return {'endpoint': self.endpoint,
                'keys': {'p256dh': self.p256dh, 'auth': self.auth}}

class Notification(models.Model):
    TYPE_CHOICES = [
        ('info', 'Informação'),
        ('success', 'Sucesso'),
        ('warning', 'Aviso'),
        ('error', 'Erro'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    message = models.TextField()
    notification_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='info')
    is_read = models.BooleanField(default=False)
    related_account = models.ForeignKey(InstagramAccount, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
