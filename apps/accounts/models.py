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
    created_at = models.DateTimeField(auto_now_add=True)
