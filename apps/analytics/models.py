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
