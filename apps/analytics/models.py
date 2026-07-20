from django.db import models
from apps.instagram.models import InstagramAccount

class DailySnapshot(models.Model):
    account = models.ForeignKey(InstagramAccount, on_delete=models.CASCADE)
    date = models.DateField()
    followers = models.IntegerField()
    following = models.IntegerField()
    posts = models.IntegerField()
    engagement_rate = models.FloatField(null=True, blank=True)

    class Meta:
        unique_together = ['account', 'date']

class SystemLog(models.Model):
    LEVEL_CHOICES = [
        ('info', 'Info'),
        ('warning', 'Aviso'),
        ('error', 'Erro'),
        ('success', 'Sucesso')
    ]
    owner = models.ForeignKey('accounts.User', on_delete=models.CASCADE)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default='info')
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
