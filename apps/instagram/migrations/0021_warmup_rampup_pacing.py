from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('instagram', '0020_instagramaccount_meta_token_expira_em'),
    ]

    operations = [
        migrations.AddField(
            model_name='warmupconfig',
            name='browse_today',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='warmupconfig',
            name='started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='warmupconfig',
            name='last_action_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
