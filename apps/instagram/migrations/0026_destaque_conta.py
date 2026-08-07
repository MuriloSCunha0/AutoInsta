# -*- coding: utf-8 -*-
"""Destaque ("LINK") do perfil: pk e título do destaque onde a conta fixa os
stories com link. Ver engine.client.InstagramEngine.fixar_no_destaque."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('instagram', '0025_restricao_conta'),
    ]

    operations = [
        migrations.AddField(
            model_name='instagramaccount',
            name='destaque_pk',
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name='instagramaccount',
            name='destaque_titulo',
            field=models.CharField(blank=True, max_length=40),
        ),
    ]
