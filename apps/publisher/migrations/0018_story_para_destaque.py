# -*- coding: utf-8 -*-
"""Story que, depois de publicado, é fixado no destaque do perfil."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('publisher', '0017_remover_clean_mode_light'),
    ]

    operations = [
        migrations.AddField(
            model_name='scheduledpost',
            name='para_destaque',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='scheduledpost',
            name='destaque_titulo',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
    ]
