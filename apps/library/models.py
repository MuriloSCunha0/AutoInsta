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
    file = models.FileField(upload_to='audios/')
    duration_seconds = models.FloatField(null=True, blank=True)
    used_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
