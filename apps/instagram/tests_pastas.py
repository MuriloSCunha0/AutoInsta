"""Pastas de contas, contagem de conectadas e ordem da fila."""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from apps.accounts.models import User
from apps.instagram.models import InstagramAccount, Pasta
from apps.publisher.models import ScheduledPost


class PastaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dono', password='x', is_active=True)
        self.client.force_login(self.user)
        self.a = InstagramAccount.objects.create(owner=self.user, ig_username='a', status='active')
        self.b = InstagramAccount.objects.create(owner=self.user, ig_username='b', status='active')

    def test_cria_pasta_e_move_conta(self):
        self.client.post(reverse('instagram:criar_pasta'), {'name': 'Premium'})
        pasta = Pasta.objects.get(owner=self.user, name='Premium')
        self.client.post(reverse('instagram:set_pasta', args=[self.a.id]), {'pasta': pasta.id})
        self.a.refresh_from_db()
        self.assertEqual(self.a.pasta_id, pasta.id)

    def test_filtra_lista_por_pasta(self):
        pasta = Pasta.objects.create(owner=self.user, name='P1')
        self.a.pasta = pasta
        self.a.save()
        resp = self.client.get(reverse('instagram:list'), {'pasta': pasta.id})
        ids = {c.id for c in resp.context['accounts']}
        self.assertEqual(ids, {self.a.id})

    def test_excluir_pasta_nao_apaga_contas(self):
        pasta = Pasta.objects.create(owner=self.user, name='P1')
        self.a.pasta = pasta
        self.a.save()
        self.client.post(reverse('instagram:delete_pasta', args=[pasta.id]))
        self.a.refresh_from_db()
        self.assertTrue(InstagramAccount.objects.filter(id=self.a.id).exists())
        self.assertIsNone(self.a.pasta_id)

    def test_nao_move_para_pasta_de_outro_usuario(self):
        outro = User.objects.create_user(username='o', password='x')
        alheia = Pasta.objects.create(owner=outro, name='X')
        self.client.post(reverse('instagram:set_pasta', args=[self.a.id]), {'pasta': alheia.id})
        self.a.refresh_from_db()
        self.assertIsNone(self.a.pasta_id)


class ContasConectadasTest(TestCase):
    def test_conta_desconectada_sai_da_contagem(self):
        u = User.objects.create_user(username='dono', password='x', is_active=True)
        self.client.force_login(u)
        InstagramAccount.objects.create(owner=u, ig_username='ok', status='active')
        InstagramAccount.objects.create(owner=u, ig_username='caiu', status='error')
        InstagramAccount.objects.create(owner=u, ig_username='ban', status='banned')
        resp = self.client.get(reverse('analytics:dashboard'))
        # só a 'active' conta como conectada
        self.assertEqual(resp.context['accounts_count'], 1)


class OrdemFilaTest(TestCase):
    def test_fila_mostra_mais_proximas_primeiro(self):
        u = User.objects.create_user(username='dono', password='x', is_active=True)
        self.client.force_login(u)
        acc = InstagramAccount.objects.create(owner=u, ig_username='a')
        agora = timezone.now()
        longe = ScheduledPost.objects.create(owner=u, account=acc, post_type='REELS',
                                             status='queued', scheduled_for=agora + timedelta(hours=5))
        perto = ScheduledPost.objects.create(owner=u, account=acc, post_type='REELS',
                                             status='queued', scheduled_for=agora + timedelta(minutes=5))
        resp = self.client.get(reverse('publisher:queue'))
        ids = [p.id for p in resp.context['posts']]
        self.assertLess(ids.index(perto.id), ids.index(longe.id))  # perto vem antes
