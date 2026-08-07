"""Plano de volume do ATOM: cadência, anti-repetição, story-link e destaque.

O plano é agressivo de propósito (pedido do usuário), então o que estes testes
protegem é justamente o que impede o volume de virar padrão robótico óbvio:

  - o INTERVALO pedido vale de verdade (o horário agendado é a fonte da verdade
    do dispatcher — pedir 40 min e agendar tudo junto não adianta nada);
  - a mesma conta não repete a mesma mídia em seguida;
  - contas diferentes não saem com a mesma mídia nem com o mesmo CTA no mesmo
    ciclo (18 contas postando o mesmo vídeo com o mesmo texto no mesmo minuto é
    o padrão coordenado mais fácil de detectar que existe);
  - story com link só em conta COM sessão, e só o 1º do dia vai para o destaque;
  - o destaque falhar nunca derruba um story que já publicou.

    python manage.py test apps.publisher.tests_plano_atom
"""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.instagram.models import InstagramAccount
from apps.library.models import MediaAsset, MediaFolder
from apps.publisher import cta_atom
from apps.publisher.models import ScheduledPost


class PlanoAtomTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='iorio', password='x')
        self.pasta = MediaFolder.objects.create(owner=self.user, name='ATOM')
        for i in range(6):
            MediaAsset.objects.create(
                owner=self.user, folder=self.pasta, kind='video',
                file=f'media_library/v{i}.mp4', original_name=f'v{i}.mp4')
        # 3 contas: 2 com sessao (story-link/destaque), 1 so'-token.
        self.contas = []
        for i in range(3):
            c = InstagramAccount.objects.create(
                owner=self.user, ig_username=f'atom{i}', status='active',
                modelo='atom', meta_access_token='tok', ig_user_id=100 + i,
                session_blob=('{"x":1}' if i < 2 else ''))
            self.contas.append(c)

    def _rodar(self, **extra):
        out = StringIO()
        opts = dict(user='iorio', modelo='atom', pasta='ATOM', dias=1,
                    intervalo=40, inicio='08:00', fim='23:00',
                    stories_dia=2, stdout=out, stderr=StringIO())
        opts.update(extra)
        # A arte do story usa Pillow/ffmpeg e escreve no MEDIA_ROOT — fora do
        # escopo destes testes, que checam o PLANO.
        with mock.patch('apps.publisher.management.commands.plano_atom.Command._arte_story',
                        return_value='cta_atom/fake.jpg'):
            call_command('plano_atom', **opts)
        return out.getvalue()

    def _reels(self, conta):
        return list(ScheduledPost.objects.filter(
            account=conta, post_type='REELS').order_by('scheduled_for'))

    # ── cadência ─────────────────────────────────────────────────────────────
    def test_intervalo_pedido_vale_de_verdade(self):
        self._rodar(intervalo=40)
        posts = self._reels(self.contas[0])
        self.assertGreater(len(posts), 5)
        for a, b in zip(posts, posts[1:]):
            self.assertEqual(b.scheduled_for - a.scheduled_for, timedelta(minutes=40))
        # E o dispatcher precisa do intervalo NO POST para aplicar o anti-rajada.
        self.assertTrue(all(p.interval_minutes == 40 for p in posts))

    def test_nao_agenda_fora_da_janela(self):
        self._rodar(inicio='09:00', fim='18:00')
        for p in self._reels(self.contas[0]):
            hora = timezone.localtime(p.scheduled_for).hour
            self.assertGreaterEqual(hora, 9)
            self.assertLessEqual(hora, 18)

    def test_nao_agenda_no_passado(self):
        self._rodar()
        agora = timezone.now()
        for p in self._reels(self.contas[0]):
            self.assertGreater(p.scheduled_for, agora)

    # ── anti-repetição ───────────────────────────────────────────────────────
    def test_mesma_conta_nao_repete_midia_seguida(self):
        self._rodar()
        for conta in self.contas:
            nomes = [p.video_file.name for p in self._reels(conta)]
            for a, b in zip(nomes, nomes[1:]):
                self.assertNotEqual(a, b, f'@{conta.ig_username} repetiu {a} em seguida')

    def test_mesma_conta_da_a_volta_antes_de_repetir(self):
        # Com 6 videos, os 6 primeiros posts tem de ser os 6 videos distintos.
        self._rodar()
        nomes = [p.video_file.name for p in self._reels(self.contas[0])][:6]
        self.assertEqual(len(set(nomes)), 6)

    def test_contas_diferentes_nao_saem_iguais_no_mesmo_ciclo(self):
        self._rodar()
        a = [p.video_file.name for p in self._reels(self.contas[0])]
        b = [p.video_file.name for p in self._reels(self.contas[1])]
        iguais = sum(1 for x, y in zip(a, b) if x == y)
        # Coincidencia pontual e' inevitavel (a pasta e' finita); o que nao pode
        # e' a fila inteira sair sincronizada.
        self.assertLess(iguais, len(a) * 0.5)

    def test_nenhuma_legenda_repetida_no_mesmo_horario(self):
        # Bug real em produção: o rodízio era deslocado pelo ID da conta, e ids
        # que diferem por múltiplo do tamanho do banco (490 e 634, banco de 24)
        # caíam no MESMO modelo — as duas contas publicaram texto idêntico no
        # mesmo minuto, o padrão coordenado que o banco existe para evitar.
        # Ids esparsos e não-consecutivos, como em produção:
        InstagramAccount.objects.all().delete()
        for novo_id in (490, 514, 634, 658):
            c = InstagramAccount.objects.create(
                owner=self.user, ig_username=f'c{novo_id}', status='active',
                modelo='atom', meta_access_token='tok', ig_user_id=novo_id)
            InstagramAccount.objects.filter(pk=c.pk).update(id=novo_id)
        self._rodar()
        por_horario = {}
        for p in ScheduledPost.objects.filter(post_type='REELS'):
            por_horario.setdefault(p.scheduled_for, []).append(p.caption)
        for quando, textos in por_horario.items():
            self.assertEqual(len(textos), len(set(textos)),
                             f'{quando}: legenda repetida entre contas -> {textos}')

    def test_cta_varia_entre_posts_e_entre_contas(self):
        self._rodar()
        a = [p.caption for p in self._reels(self.contas[0])]
        for x, y in zip(a, a[1:]):
            self.assertNotEqual(x, y)
        b = [p.caption for p in self._reels(self.contas[1])]
        self.assertNotEqual(a[0], b[0])

    def test_legenda_gravada_ja_vem_resolvida(self):
        # A fila mostrava "{só|apenas} {pra quem|quem} {clica|entra}..." porque o
        # spintax só era resolvido no publish. Publicava certo, mas o usuário não
        # conseguia revisar o que ia sair (queixa dele, com print da tela).
        self._rodar()
        for p in self._reels(self.contas[0]):
            self.assertNotIn('{', p.caption, f'spintax cru na fila: {p.caption!r}')
            self.assertNotIn('|', p.caption)
            self.assertTrue(p.caption.strip())

    def test_todo_cta_manda_para_os_destaques(self):
        self._rodar()
        for p in self._reels(self.contas[0]):
            self.assertIn('destaque', p.caption.lower())

    # ── story com link + destaque ────────────────────────────────────────────
    def test_story_link_so_em_conta_com_sessao(self):
        self._rodar(link='https://exemplo.test/')
        com = ScheduledPost.objects.filter(account=self.contas[0], post_type='STORY')
        sem = ScheduledPost.objects.filter(account=self.contas[2], post_type='STORY')
        self.assertTrue(com.exists())
        self.assertFalse(sem.exists())
        self.assertTrue(all(s.story_link == 'https://exemplo.test/' for s in com))

    def test_so_o_primeiro_story_do_dia_vai_para_o_destaque(self):
        # 2 dias: amanhã sempre cabe a grade inteira, hoje depende da hora em
        # que o comando rodou — por isso a contagem é POR DIA.
        self._rodar(dias=2, stories_dia=3)
        stories = list(ScheduledPost.objects.filter(
            account=self.contas[0], post_type='STORY').order_by('scheduled_for'))
        self.assertTrue(stories)
        por_dia = {}
        for s in stories:
            por_dia.setdefault(timezone.localtime(s.scheduled_for).date(), []).append(s)
        for dia, do_dia in por_dia.items():
            marcados = [s for s in do_dia if s.para_destaque]
            self.assertEqual(len(marcados), 1, f'{dia}: esperava 1 story no destaque')
            # E tem de ser o PRIMEIRO do dia, não um do meio.
            self.assertIs(marcados[0], do_dia[0])
            self.assertEqual(marcados[0].destaque_titulo, cta_atom.DESTAQUE_TITULO)
        # O dia cheio (amanhã) tem a grade completa.
        self.assertEqual(max(len(v) for v in por_dia.values()), 3)

    def test_marca_o_destaque_mesmo_rodando_tarde(self):
        # Bug real: com "primeiro do dia" = primeiro da grade teórica, rodar o
        # comando depois desse horário deixava o dia inteiro sem destaque.
        from apps.publisher.management.commands.plano_atom import Command
        h_ini, h_fim = Command()._hora('08:00', 'i'), Command()._hora('23:00', 'f')
        # 14h: a grade teórica é 11:45 / 15:30 / 19:15 — a primeira já passou.
        with mock.patch('apps.publisher.management.commands.plano_atom.timezone.localtime',
                        return_value=timezone.localtime().replace(hour=14, minute=0)):
            slots = Command()._horarios_story(1, 3, h_ini, h_fim)
        self.assertEqual(len(slots), 2)
        self.assertTrue(slots[0][1], 'o primeiro que SOBROU tem de ir para o destaque')
        self.assertFalse(slots[1][1])

    def test_dry_run_nao_grava_nada(self):
        saida = self._rodar(dry_run=True)
        self.assertEqual(ScheduledPost.objects.count(), 0)
        self.assertIn('Nada foi gravado', saida)

    def test_avisa_quais_contas_estao_sem_sessao(self):
        saida = self._rodar(dry_run=True)
        self.assertIn('SEM SESSÃO', saida)
        self.assertIn('atom2', saida)


class DestaqueNoPublishTest(TestCase):
    """O story publicou: entra no destaque — e se o destaque falhar, o post
    continua publicado (destaque é enfeite, publicação é o produto)."""

    def setUp(self):
        self.user = User.objects.create_user(username='iorio', password='x')
        self.conta = InstagramAccount.objects.create(
            owner=self.user, ig_username='atom', status='active',
            session_blob='{"x":1}')
        self.post = ScheduledPost.objects.create(
            owner=self.user, account=self.conta, post_type='STORY',
            status='processing', scheduled_for=timezone.now(),
            story_link='https://exemplo.test/', para_destaque=True,
            destaque_titulo='LINK 🔗')
        self.post.video_file.name = 'cta_atom/x.jpg'
        self.post.save()

    def _publica(self, destaque_erro=None):
        with mock.patch('apps.publisher.tasks.InstagramEngine') as Eng, \
             mock.patch('apps.core_utils.garantir_midia_local',
                        return_value=('/tmp/fake.jpg', False)):
            eng = Eng.return_value
            eng.upload_story.return_value = {'pk': '555'}
            if destaque_erro:
                eng.fixar_no_destaque.side_effect = Exception(destaque_erro)
            from apps.publisher.tasks import publish_reel
            publish_reel(self.post.id)
            self.post.refresh_from_db()
            return eng

    def test_story_publicado_entra_no_destaque(self):
        eng = self._publica()
        self.assertEqual(self.post.status, 'published')
        eng.fixar_no_destaque.assert_called_once()
        self.assertEqual(eng.fixar_no_destaque.call_args.kwargs['titulo'], 'LINK 🔗')

    def test_falha_no_destaque_nao_derruba_o_post(self):
        self._publica(destaque_erro='highlight_create falhou')
        self.assertEqual(self.post.status, 'published')

    def test_story_sem_marcacao_nao_chama_destaque(self):
        self.post.para_destaque = False
        self.post.status = 'processing'
        self.post.save(update_fields=['para_destaque', 'status'])
        eng = self._publica()
        eng.fixar_no_destaque.assert_not_called()
