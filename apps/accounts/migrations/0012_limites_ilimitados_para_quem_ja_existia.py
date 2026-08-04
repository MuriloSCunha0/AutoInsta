"""Quem já existia fica ILIMITADO.

`max_ig_accounts` existia com default 3 mas NUNCA era aplicado — era só um campo
no admin do Django. A partir de agora ele vale de verdade.

Sem esta migração, no segundo do deploy todo usuário com mais de 3 contas
(em produção: 50, 31, 25, 19...) ficaria impedido de conectar qualquer conta
nova, e o suporte descobriria pelo reclame. Zeramos o limite de quem já existe
(0 = ilimitado); o admin passa a definir o teto conta a conta, de propósito.

Usuários NOVOS também nascem com 0: quem quiser vender por plano define o
limite no cadastro ou na tela de gestão.
"""
from django.db import migrations


def soltar_limites_existentes(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.update(max_ig_accounts=0, max_meta_apps=0)


def nao_da_para_voltar(apps, schema_editor):
    """Reverter recolocaria o 3 que nunca valeu — e travaria a operação."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0011_limites_contas_e_apps'),
    ]

    operations = [
        migrations.RunPython(soltar_limites_existentes, nao_da_para_voltar),
    ]
