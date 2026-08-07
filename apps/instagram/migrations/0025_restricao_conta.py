# -*- coding: utf-8 -*-
"""Restrição da conta (Meta code 25) deixa de ser tratada como "limitada".

Campos novos (`restricao_count` / `restricao_desde`) e uma limpeza do estado que
o tratamento ANTIGO deixou no banco: ele marcava cooldown de 3h já na 1ª recusa
e REGRAVAVA esse cooldown a cada nova recusa, então as contas ficavam
permanentemente "Limitada pela Meta" com uma contagem regressiva que nunca
chegava a zero. Aqui zeramos esse cooldown herdado para as contas que o
receberam por restrição (identificadas pela mensagem que aquele handler gravava
em `last_error`) — e SÓ para elas: quem está em cooldown por limite real de cota
(`meta_limit_count > 0`) continua como está.
"""
from django.db import migrations, models

MARCA_ANTIGA = 'Publicação temporariamente restringida pela Meta'


def soltar_cooldown_herdado(apps, schema_editor):
    InstagramAccount = apps.get_model('instagram', 'InstagramAccount')
    InstagramAccount.objects.filter(
        last_error__startswith=MARCA_ANTIGA,
        rate_limited_until__isnull=False,
        meta_limit_count=0,
    ).update(rate_limited_until=None, last_error='')


def noop(apps, schema_editor):
    """Sem volta: o cooldown herdado era um efeito do bug, não um dado."""


class Migration(migrations.Migration):

    dependencies = [
        ('instagram', '0024_instagramaccount_publicados_total'),
    ]

    operations = [
        migrations.AddField(
            model_name='instagramaccount',
            name='restricao_count',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='instagramaccount',
            name='restricao_desde',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(soltar_cooldown_herdado, noop),
    ]
