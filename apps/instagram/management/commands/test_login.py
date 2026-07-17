"""Testa o fluxo de login de uma conta do Instagram de forma interativa.

Uso:
    python manage.py test_login <ig_username>
    python manage.py test_login <ig_username> --owner meu_usuario --proxy http://user:pass@ip:port
    python manage.py test_login <ig_username> --use-saved   # reusa senha/sessão já salvas

A senha é pedida de forma segura (getpass) — nada é colado em código nem
fica no histórico do shell. Reaproveita a mesma conta se ela já existir,
para exercitar o reuso de sessão e o device fixo.
"""
import getpass

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.instagram.models import InstagramAccount
from engine.client import InstagramEngine


class Command(BaseCommand):
    help = "Testa o login de uma conta do Instagram (interativo, seguro)."

    def add_arguments(self, parser):
        parser.add_argument('ig_username', help='@ da conta do Instagram a testar')
        parser.add_argument('--owner', help='username do dono na plataforma (padrão: 1º superuser)')
        parser.add_argument('--proxy', help='proxy opcional (http://user:pass@ip:port)')
        parser.add_argument('--use-saved', action='store_true',
                            help='usa a senha/sessão já salvas, sem pedir de novo')

    def handle(self, *args, **opts):
        ig_username = opts['ig_username'].lstrip('@').strip()

        # Descobre o dono na plataforma.
        if opts.get('owner'):
            owner = User.objects.filter(username=opts['owner']).first()
            if not owner:
                raise CommandError(f"Usuário '{opts['owner']}' não encontrado.")
        else:
            owner = User.objects.filter(is_superuser=True).first() or User.objects.first()
            if not owner:
                raise CommandError("Nenhum usuário na base. Crie um superuser primeiro.")

        # Reusa a conta se já existir (testa reuso de sessão/device).
        account, created = InstagramAccount.objects.get_or_create(
            owner=owner, ig_username=ig_username,
            defaults={'status': 'connecting'},
        )

        if opts.get('proxy'):
            account.proxy_url = opts['proxy']

        if not opts['use_saved'] or not account.ig_password:
            pw = getpass.getpass(f"Senha do Instagram para @{ig_username}: ")
            if not pw:
                raise CommandError("Senha vazia.")
            account.set_ig_password(pw)

        account.status = 'connecting'
        account.save()

        self.stdout.write(self.style.WARNING(
            f"→ Testando login de @{ig_username} (dono: {owner.username}, "
            f"conta {'nova' if created else 'existente'}, "
            f"proxy: {account.proxy_url or 'nenhum'})..."
        ))

        engine = InstagramEngine(account)
        try:
            engine.login()
        except Exception as e:
            # O engine já grava status/last_error; aqui só reportamos.
            self.stdout.write(self.style.ERROR(f"✖ Exceção: {type(e).__name__}: {e}"))

        account.refresh_from_db()

        status_styles = {
            'active': self.style.SUCCESS,
            'challenge_required': self.style.WARNING,
            '2fa_required': self.style.WARNING,
        }
        style = status_styles.get(account.status, self.style.ERROR)
        self.stdout.write(style(f"\nStatus final: {account.status}"))
        if account.last_error:
            self.stdout.write(f"Detalhe: {account.last_error}")
        if account.status == 'active':
            self.stdout.write(self.style.SUCCESS(
                f"✔ @{account.ig_username} — {account.followers_count} seguidores, "
                f"{account.posts_count} posts. Login OK!"
            ))
        elif account.status in ('challenge_required', '2fa_required'):
            self.stdout.write(
                "→ Caiu em verificação. Resolva pelo card da conta na plataforma "
                "('Resolver') ou pelas tasks submit_challenge_code / submit_2fa_code."
            )
