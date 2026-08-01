from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('instagram', '0018_alter_instagramaccount_daily_post_limit'),
    ]

    operations = [
        migrations.AddField(
            model_name='instagramaccount',
            name='sessao_expirada',
            field=models.BooleanField(default=False),
        ),
    ]
