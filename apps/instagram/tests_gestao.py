"""Gestão de contas: a visão ON/OFF do próprio usuário.

A tela de Contas é feita de cards grandes — boa para conectar e configurar,
ruim para responder "quantas estão no ar agora?" com 50 contas. Aqui é a visão
de controle: uma linha por conta, ON/OFF explícito e o motivo de quem está fora.

    python manage.py test apps.instagram.tests_gestao
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.instagram.models import InstagramAccount, Pasta


class GestaoTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d', password='x')
        self.outro = User.objects.create_user(username='o', password='x')
        self.client.force_login(self.user)
        self.pasta = Pasta.objects.create(owner=self.user, name='Op 1')

        self.no_ar = InstagramAccount.objects.create(
            owner=self.user, ig_username='viva', status='active', pasta=self.pasta)
        self.pausada = InstagramAccount.objects.create(
            owner=self.user, ig_username='parada', status='active', pausada=True)
        self.caida = InstagramAccount.objects.create(
            owner=self.user, ig_username='caiu', status='error')

    def test_tela_abre(self):
        r = self.client.get(reverse('instagram:gestao'))
        self.assertEqual(r.status_code, 200)

    def test_exige_login(self):
        self.client.logout()
        r = self.client.get(reverse('instagram:gestao'))
        self.assertEqual(r.status_code, 302)

    def test_conta_no_ar_e_as_de_fora(self):
        r = self.client.get(reverse('instagram:gestao'))
        self.assertEqual(r.context['n_on'], 1)
        self.assertEqual(r.context['n_off'], 2)

    def test_mostra_o_motivo_de_cada_uma_fora(self):
        r = self.client.get(reverse('instagram:gestao'))
        rotulos = {i['conta'].ig_username: i['rotulo'] for i in r.context['itens']}
        self.assertEqual(rotulos['viva'], 'no ar')
        self.assertEqual(rotulos['parada'], 'pausada')
        self.assertEqual(rotulos['caiu'], 'conta caiu')

    def test_conta_em_cooldown_conta_como_fora(self):
        self.no_ar.rate_limited_until = timezone.now() + timezone.timedelta(hours=2)
        self.no_ar.save()
        r = self.client.get(reverse('instagram:gestao'))
        self.assertEqual(r.context['n_on'], 0)

    def test_filtro_so_no_ar(self):
        r = self.client.get(reverse('instagram:gestao'), {'situacao': 'on'})
        self.assertEqual(len(r.context['itens']), 1)

    def test_filtro_so_fora(self):
        r = self.client.get(reverse('instagram:gestao'), {'situacao': 'off'})
        self.assertEqual(len(r.context['itens']), 2)

    def test_filtro_por_pasta(self):
        r = self.client.get(reverse('instagram:gestao'), {'pasta': self.pasta.id})
        self.assertEqual(len(r.context['itens']), 1)

    def test_filtro_sem_pasta(self):
        r = self.client.get(reverse('instagram:gestao'), {'pasta': 'sem'})
        self.assertEqual(len(r.context['itens']), 2)

    def test_busca_por_arroba(self):
        r = self.client.get(reverse('instagram:gestao'), {'q': '@viva'})
        self.assertEqual(len(r.context['itens']), 1)

    def test_nao_mostra_conta_de_outro_dono(self):
        InstagramAccount.objects.create(
            owner=self.outro, ig_username='alheia', status='active')
        r = self.client.get(reverse('instagram:gestao'))
        nomes = [i['conta'].ig_username for i in r.context['itens']]
        self.assertNotIn('alheia', nomes)

    def test_mostra_o_uso_do_limite(self):
        self.user.max_ig_accounts = 10
        self.user.save()
        r = self.client.get(reverse('instagram:gestao'))
        self.assertEqual(r.context['limite'], 10)
        self.assertEqual(r.context['usadas'], 3)


class GestaoEmMassaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='d2', password='x')
        self.outro = User.objects.create_user(username='o2', password='x')
        self.client.force_login(self.user)
        self.a = InstagramAccount.objects.create(
            owner=self.user, ig_username='a', status='active')
        self.b = InstagramAccount.objects.create(
            owner=self.user, ig_username='b', status='active')

    def test_pausa_varias(self):
        self.client.post(reverse('instagram:gestao_massa'),
                         {'acao': 'pausar', 'contas': [self.a.id, self.b.id]})
        self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertTrue(self.a.pausada)
        self.assertTrue(self.b.pausada)

    def test_retomar_limpa_o_contador_de_limites(self):
        # Sem zerar, o próximo limite da Meta já jogaria a conta de molho.
        self.a.pausada = True
        self.a.meta_limit_count = 2
        self.a.save()
        self.client.post(reverse('instagram:gestao_massa'),
                         {'acao': 'retomar', 'contas': [self.a.id]})
        self.a.refresh_from_db()
        self.assertFalse(self.a.pausada)
        self.assertEqual(self.a.meta_limit_count, 0)

    def test_nao_mexe_em_conta_de_outro_dono(self):
        alheia = InstagramAccount.objects.create(
            owner=self.outro, ig_username='alheia', status='active')
        self.client.post(reverse('instagram:gestao_massa'),
                         {'acao': 'pausar', 'contas': [alheia.id]})
        alheia.refresh_from_db()
        self.assertFalse(alheia.pausada)

    def test_acao_desconhecida_nao_faz_nada(self):
        self.client.post(reverse('instagram:gestao_massa'),
                         {'acao': 'explodir', 'contas': [self.a.id]})
        self.a.refresh_from_db()
        self.assertFalse(self.a.pausada)
