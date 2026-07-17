from django.db import models
from apps.accounts.models import User
from apps.instagram.models import InstagramAccount

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
