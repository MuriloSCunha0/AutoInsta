from django.db import migrations


def criar_app_a_partir_das_credenciais(apps, schema_editor):
    """Move as credenciais Meta que já estavam no User para um MetaApp.

    Assim quem já tinha configurado o app não perde nada ao migrarmos para o
    cadastro de múltiplos apps. Os secrets já estão criptografados: copiamos
    o texto cifrado como está (mesma FERNET_KEY), sem precisar decifrar.
    """
    User = apps.get_model('accounts', 'User')
    MetaApp = apps.get_model('accounts', 'MetaApp')

    for user in User.objects.all():
        tem_credencial = (user.meta_app_id or '').strip() or (user.meta_app_secret_enc or '')
        if not tem_credencial:
            continue
        if MetaApp.objects.filter(owner=user).exists():
            continue

        MetaApp.objects.create(
            owner=user,
            name='App principal',
            meta_app_id=user.meta_app_id or '',
            meta_app_secret_enc=user.meta_app_secret_enc or '',
            meta_login_config_id=user.meta_login_config_id or '',
            instagram_app_id=user.instagram_app_id or '',
            instagram_app_secret_enc=user.instagram_app_secret_enc or '',
            is_active=True,
        )


def desfazer(apps, schema_editor):
    # Nada a desfazer: os campos originais no User continuam intactos.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_metaapp'),
    ]

    operations = [
        migrations.RunPython(criar_app_a_partir_das_credenciais, desfazer),
    ]
