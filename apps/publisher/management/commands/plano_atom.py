# -*- coding: utf-8 -*-
"""Monta o plano de volume do ATOM: reels de X em X minutos + stories com link
fixados no destaque, com CTA variado e anti-repetição.

    python manage.py plano_atom --user iorio --modelo atom --pasta "ATOM" \
        --dias 3 --intervalo 40 --dry-run

O que ele faz por conta:
  - enfileira um reel a cada `--intervalo` minutos, dentro da janela do dia
    (`--inicio`/`--fim`), por `--dias` dias;
  - escolhe a mídia por RODÍZIO EMBARALHADO por conta: duas contas não postam o
    mesmo vídeo no mesmo ciclo, e a mesma conta só repete um vídeo depois de dar
    a volta na pasta inteira (com o embaralhamento trocando a cada volta, para a
    sequência também não se repetir);
  - escreve a legenda com o banco de CTAs (`cta_atom`), em rodízio deslocado por
    conta e com spintax — cada post sai com um texto diferente;
  - nas contas COM SESSÃO, cria `--stories-dia` stories por dia com o adesivo de
    link apontando para `--link`, e marca o primeiro do dia para ser FIXADO NO
    DESTAQUE (o story morre em 24h, o destaque fica — é o que faz "link nos
    destaques" continuar verdade).

Conta só-token não entra no story-link nem no destaque: nada disso existe na API
oficial da Meta. Elas recebem só os reels, e o comando avisa quais são.

Sempre rode com `--dry-run` primeiro: ele imprime o plano inteiro sem gravar
nada.
"""
import os
import random
from datetime import datetime, time as dtime, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import User
from apps.instagram.models import InstagramAccount
from apps.library.models import MediaAsset, MediaFolder
from apps.publisher import cta_atom
from apps.publisher.caption_utils import _rng, expandir_spintax
from apps.publisher.models import PostQueue, ScheduledPost

LINK_PADRAO = 'https://thriving-dragon-ada2b5.netlify.app/'


class Command(BaseCommand):
    help = 'Monta a fila de volume do ATOM (reels em intervalo fixo + story-link + destaque).'

    def add_arguments(self, p):
        p.add_argument('--user', required=True, help='username do dono das contas')
        p.add_argument('--modelo', default='', help='filtra contas por "modelo" (ex.: atom)')
        p.add_argument('--contas', default='', help='ids ou @usernames separados por vírgula')
        p.add_argument('--pasta', default='', help='pasta da biblioteca com os reels')
        p.add_argument('--dias', type=int, default=3)
        p.add_argument('--intervalo', type=int, default=40, help='minutos entre reels da MESMA conta')
        p.add_argument('--inicio', default='08:00', help='início da janela diária (local)')
        p.add_argument('--fim', default='23:00', help='fim da janela diária (local)')
        p.add_argument('--link', default=LINK_PADRAO)
        p.add_argument('--stories-dia', type=int, default=3)
        p.add_argument('--fila', default='ATOM volume')
        p.add_argument('--teto', type=int, default=None,
                       help='grava daily_post_limit nas contas (0 = sem teto)')
        p.add_argument('--dry-run', action='store_true')

    # ── helpers ──────────────────────────────────────────────────────────────

    def _hora(self, txt, campo):
        try:
            h, m = txt.split(':')
            return dtime(int(h), int(m))
        except Exception:
            raise CommandError(f'--{campo} deve ser HH:MM (recebi {txt!r})')

    def _slots(self, dias, intervalo, janela_ini, janela_fim):
        """Horários de reel: passo fixo dentro da janela, por `dias` dias.

        O horário AGENDADO é a fonte da verdade do dispatcher, então é aqui que
        o intervalo de verdade é definido — não adianta pedir 40 min e agendar
        tudo junto.
        """
        agora = timezone.localtime()
        saida = []
        for d in range(dias):
            dia = (agora + timedelta(days=d)).date()
            t = timezone.make_aware(datetime.combine(dia, janela_ini))
            fim = timezone.make_aware(datetime.combine(dia, janela_fim))
            while t <= fim:
                if t > agora:
                    saida.append(t)
                t += timedelta(minutes=intervalo)
        return saida

    def _horarios_story(self, dias, quantos, janela_ini, janela_fim):
        """Stories espalhados pela janela. Story é ISENTO das travas de volume do
        feed no dispatcher, então tem grade própria e não rouba vaga de reel.

        Devolve (dia, é_o_primeiro_do_dia, quando). "Primeiro do dia" é o
        primeiro que SOBROU depois de descartar os horários que já passaram —
        se fosse o primeiro da grade teórica, rodar o comando de tarde deixava o
        dia inteiro sem nenhum story marcado para o destaque.
        """
        agora = timezone.localtime()
        saida = []
        if quantos <= 0:
            return saida
        for d in range(dias):
            dia = (agora + timedelta(days=d)).date()
            ini = timezone.make_aware(datetime.combine(dia, janela_ini))
            fim = timezone.make_aware(datetime.combine(dia, janela_fim))
            vao = (fim - ini) / (quantos + 1)
            do_dia = [ini + vao * k for k in range(1, quantos + 1)]
            do_dia = [t for t in do_dia if t > agora]
            for pos, t in enumerate(do_dia):
                saida.append((d, pos == 0, t))
        return saida

    def _rodizio_midias(self, assets, conta_id, quantos):
        """Sequência de mídias sem repetir de perto.

        Embaralha a pasta com semente por conta+volta: a mesma conta só revê um
        vídeo depois de passar por todos, e a ordem muda a cada volta (senão a
        sequência inteira se repetiria igual). Na virada da volta, garante que o
        primeiro item não é o último da volta anterior — que seria o único jeito
        de sair a mesma mídia duas vezes seguidas.
        """
        if not assets:
            return []
        saida = []
        volta = 0
        while len(saida) < quantos:
            ordem = list(assets)
            random.Random(f'{conta_id}-{volta}').shuffle(ordem)
            if saida and len(ordem) > 1 and ordem[0].id == saida[-1].id:
                ordem[0], ordem[-1] = ordem[-1], ordem[0]
            saida.extend(ordem)
            volta += 1
        return saida[:quantos]

    def _arte_story(self, conta, i, base_video, destino_dir):
        """Gera o JPG do story: frame de um reel da própria conta + título +
        pílula de link. Devolve o nome relativo ao MEDIA_ROOT, ou None."""
        from engine.cta_render import gerar_cta
        fundo = None
        tmp = None
        if base_video:
            try:
                from engine.media_cleaner import extrair_thumbnail
                caminho = os.path.join(settings.MEDIA_ROOT, base_video.file.name)
                if os.path.exists(caminho):
                    tmp = extrair_thumbnail(caminho)
                    fundo = tmp
            except Exception:
                fundo = None
        rel = f'cta_atom/{conta.id}-{i}.jpg'
        destino = os.path.join(destino_dir, os.path.basename(rel))
        try:
            gerar_cta(
                base_path=fundo, tipo='link',
                titulo=cta_atom.titulo_story(i, conta.id),
                sticker_texto=cta_atom.botao_story(i, conta.id),
                titulo_y=0.15, sticker_y=0.60, escurecer=0.30,
                destino=destino,
            )
            return rel
        except Exception as e:
            self.stderr.write(f'  ! arte do story falhou (@{conta.ig_username}): {e}')
            return None
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    # ── execução ─────────────────────────────────────────────────────────────

    def handle(self, *a, **o):
        seco = o['dry_run']
        user = User.objects.filter(username=o['user']).first()
        if not user:
            raise CommandError(f'usuário {o["user"]!r} não existe')

        contas = InstagramAccount.objects.filter(owner=user, status='active',
                                                 banned_by_admin=False)
        if o['modelo']:
            contas = contas.filter(modelo__iexact=o['modelo'])
        if o['contas']:
            ids, nomes = [], []
            for parte in o['contas'].split(','):
                parte = parte.strip().lstrip('@')
                (ids if parte.isdigit() else nomes).append(parte)
            from django.db.models import Q
            contas = contas.filter(Q(id__in=[int(i) for i in ids])
                                   | Q(ig_username__in=nomes))
        contas = list(contas.order_by('id'))
        if not contas:
            raise CommandError('nenhuma conta ativa bate com o filtro')

        assets = MediaAsset.objects.filter(owner=user, kind='video')
        if o['pasta']:
            pasta = MediaFolder.objects.filter(owner=user, name__iexact=o['pasta']).first()
            if not pasta:
                raise CommandError(f'pasta {o["pasta"]!r} não existe na biblioteca')
            assets = assets.filter(folder=pasta)
        assets = list(assets.order_by('id'))
        if not assets:
            raise CommandError('nenhum vídeo na pasta — nada para agendar')

        janela_ini = self._hora(o['inicio'], 'inicio')
        janela_fim = self._hora(o['fim'], 'fim')
        slots = self._slots(o['dias'], o['intervalo'], janela_ini, janela_fim)
        if not slots:
            raise CommandError('a janela não gerou nenhum horário — confira --inicio/--fim/--dias')
        por_dia = max(1, len(slots) // max(1, o['dias']))

        destino_dir = os.path.join(settings.MEDIA_ROOT, 'cta_atom')
        if not seco:
            os.makedirs(destino_dir, exist_ok=True)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\nPLANO ATOM — {len(contas)} conta(s), {len(assets)} vídeo(s), '
            f'{len(slots)} reel(s) por conta ({por_dia}/dia, 1 a cada '
            f'{o["intervalo"]} min entre {o["inicio"]} e {o["fim"]})'))
        if por_dia > len(assets):
            self.stdout.write(self.style.WARNING(
                f'  ATENÇÃO: {por_dia} posts/dia com só {len(assets)} vídeos na pasta — '
                'cada vídeo sai mais de uma vez por dia. Repetição no mesmo dia é '
                'o que o IG mais penaliza; suba mais mídia ou aumente o intervalo.'))

        total_reels = total_stories = 0
        sem_sessao = []

        for conta in contas:
            tem_sessao = bool(getattr(conta, 'tem_sessao_engine', False))
            if not tem_sessao:
                sem_sessao.append(conta.ig_username)

            fila = None
            if not seco:
                fila, _ = PostQueue.objects.get_or_create(
                    owner=user, account=conta, name=o['fila'])

            # ── Reels ────────────────────────────────────────────────────
            midias = self._rodizio_midias(assets, conta.id, len(slots))
            for i, (quando, asset) in enumerate(zip(slots, midias)):
                # Grava a legenda JÁ RESOLVIDA. O spintax é resolvido de novo no
                # publish, então guardar o modelo cru ({a|b|c}) publicaria certo
                # — mas a fila mostraria "{só|apenas} {pra quem|quem}..." e o
                # usuário não teria como revisar o que vai sair. A variação fina
                # (invisíveis/sinônimos) continua acontecendo no publish, via
                # variar_auto.
                legenda = expandir_spintax(cta_atom.legenda(i, conta.id),
                                           _rng(f'cta-{conta.id}-{i}'))
                if not seco:
                    post = ScheduledPost(
                        owner=user, account=conta, queue=fila,
                        post_type='REELS', caption=legenda,
                        share_to_feed=True, status='queued',
                        scheduled_for=quando,
                        interval_minutes=o['intervalo'],
                        variar_auto=True,
                    )
                    post.video_file.name = asset.file.name
                    post.save()
                total_reels += 1

            # ── Stories com link + destaque (só com sessão) ──────────────
            if tem_sessao:
                for j, (_dia, primeiro_do_dia, quando) in enumerate(
                        self._horarios_story(o['dias'], o['stories_dia'],
                                             janela_ini, janela_fim)):
                    rel = None
                    if not seco:
                        rel = self._arte_story(
                            conta, j, midias[j % len(midias)] if midias else None,
                            destino_dir)
                        if not rel:
                            continue
                        post = ScheduledPost(
                            owner=user, account=conta, queue=fila,
                            post_type='STORY', caption='',
                            status='queued', scheduled_for=quando,
                            story_link=o['link'],
                            story_link_label=cta_atom.botao_story(j, conta.id),
                            # Só o 1º story do dia entra no destaque: cada
                            # inclusão é uma chamada da API privada, e o destaque
                            # não precisa de 3 cópias do mesmo CTA por dia.
                            para_destaque=primeiro_do_dia,
                            destaque_titulo=cta_atom.DESTAQUE_TITULO,
                            variar_auto=False,
                        )
                        post.video_file.name = rel
                        post.save()
                    total_stories += 1

            if o['teto'] is not None and not seco:
                conta.daily_post_limit = o['teto']
                conta.save(update_fields=['daily_post_limit'])

        # ── Resumo ───────────────────────────────────────────────────────
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{"[dry-run] " if seco else ""}{total_reels} reel(s) e {total_stories} '
            f'story(ies) para {len(contas)} conta(s).'))
        self.stdout.write(f'  Link do story/destaque: {o["link"]}')
        self.stdout.write(f'  Destaque: "{cta_atom.DESTAQUE_TITULO}" '
                          f'(criado no 1º story de cada conta com sessão)')
        if sem_sessao:
            self.stdout.write(self.style.WARNING(
                f'  SEM SESSÃO ({len(sem_sessao)}): @' + ', @'.join(sem_sessao) +
                '\n  Essas contas NÃO recebem story-link nem destaque (a API oficial '
                'da Meta não tem nenhum dos dois) — mas as legendas delas mandam o '
                'povo para os destaques. Cole o sessionid no card, ou crie o '
                'destaque à mão nessas contas.'))

        # O teto que o dispatcher vai aplicar de verdade.
        apertados = [(c.ig_username, c.teto_efetivo) for c in contas
                     if 0 < c.teto_efetivo < por_dia]
        if apertados:
            self.stdout.write(self.style.WARNING(
                f'  TETO: {len(apertados)} conta(s) têm teto menor que {por_dia}/dia — '
                'o dispatcher vai segurar o excedente e a fila acumula. Ex.: ' +
                ', '.join(f'@{u}={t}' for u, t in apertados[:5]) +
                '. Use --teto 0 para liberar (por sua conta e risco).'))
        if seco:
            self.stdout.write(self.style.NOTICE(
                '\n  Nada foi gravado. Rode de novo sem --dry-run para valer.'))
