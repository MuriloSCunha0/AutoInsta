from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('instagram', '0019_instagramaccount_sessao_expirada'),
    ]

    operations = [
        migrations.AddField(
            model_name='instagramaccount',
            name='meta_token_expira_em',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
