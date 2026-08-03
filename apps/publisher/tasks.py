import os
from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from .models import ScheduledPost, PostLoop
from engine.client import InstagramEngine


@shared_task
def process_loops():
    """Enfileira a próxima publicação de cada Loop ativo cujo intervalo venceu.

    No modo PASTA, gira as mídias em ciclo (uma por vez) usando last_index —
    assim o loop nunca repete a mesma mídia em sequência enquanto houver
    outras na pasta.
    """
    agora = timezone.now()

    for loop in PostLoop.objects.filter(is_active=True).select_related('account', 'folder', 'owner'):
        # Fila do usuário pausada? não enfileira novos.
        if loop.owner.publishing_paused:
            continue
        # Conta pausada/caída/banida: não acumula posts que só vão falhar (a
        # guarda no publish_reel evita o hit, mas isto evita a fila entupir).
        if (loop.account.pausada or loop.account.banned_by_admin
                or loop.account.status != 'active'):
            continue
        # Ainda não venceu o intervalo?
        if loop.last_posted and (agora - loop.last_posted) < timedelta(minutes=loop.interval_minutes):
            continue

        nome_arquivo = None

        if loop.folder:
            midias = loop.midias_da_pasta()
            if not midias:
                continue  # pasta vazia: nada a fazer
            indice = loop.last_index % len(midias)
            asset = midias[indice]
            nome_arquivo = asset.file.name
            loop.last_index = (indice + 1) % len(midias)
            asset.used_count += 1
            asset.save(update_fields=['used_count'])
        elif loop.video_file:
            nome_arquivo = loop.video_file.name

        if not nome_arquivo:
            continue

        post = ScheduledPost(
            owner=loop.owner,
            account=loop.account,
            post_type=loop.post_type,
            caption=loop.caption,
            share_to_feed=loop.share_to_feed,
            clean_mode=loop.clean_mode,
            audio=loop.audio,
            status='queued',
            scheduled_for=agora,
        )
        post.video_file.name = nome_arquivo
        post.save()

        loop.last_posted = agora
        loop.save(update_fields=['last_posted', 'last_index'])
        print(f"Loop {loop.id}: enfileirou post {post.id} (@{loop.account.ig_username})")

@shared_task
def process_scheduled_posts():
    """
    Tarefa periódica (Celery Beat) que despacha os posts vencidos — de forma
    CONTROLADA, para não martelar a API da Meta (o que dispara bloqueios):

      - no máximo 1 post por CONTA por rodada (espaça as publicações);
      - pula contas em cooldown de rate limit;
      - respeita o limite diário da conta (reagenda o excedente).
    """
    now = timezone.now()
    janela_24h = now - timedelta(hours=24)

    from django.db.models import F, Q

    # Rede de segurança: posts presos em 'processing' há mais de 15 min (ex.:
    # worker reiniciou e perdeu a tarefa em voo) voltam para a fila.
    presos = (ScheduledPost.objects.filter(status='processing')
              .filter(Q(processing_since__lt=now - timedelta(minutes=15))
                      | Q(processing_since__isnull=True,
                          scheduled_for__lt=now - timedelta(minutes=15))))
    n_presos = presos.update(status='queued', processing_since=None)
    if n_presos:
        print(f"Dispatcher: {n_presos} post(s) presos em 'processing' devolvidos à fila.")

    due = (ScheduledPost.objects.filter(status='queued', scheduled_for__lte=now)
           .select_related('account', 'owner', 'queue')
           # Rodízio: a fila que despachou há mais tempo vem primeiro, então
           # várias filas da mesma conta avançam de forma justa.
           # nulls_first é ESSENCIAL: no PostgreSQL, ASC joga NULL para o fim,
           # e a fila que nunca despachou (NULL) ficaria sempre por último —
           # o que fazia uma fila drenar inteira antes da outra começar.
           .order_by(F('queue__last_dispatch').asc(nulls_first=True), 'scheduled_for'))

    # Teto GLOBAL por rodada: evita o BURST de dezenas de publicações no mesmo
    # instante (visto em produção: ~25 num minuto) — a Meta lê isso como
    # atividade coordenada e pune invalidando tokens. O excedente sai nas
    # próximas rodadas (a cada ~30s). Ajustável por env sem deploy.
    from django.conf import settings as _cfg
    MAX_POR_RODADA = getattr(_cfg, 'MAX_DISPATCH_POR_RODADA', 8)

    despachadas = set()
    for post in due:
        if len(despachadas) >= MAX_POR_RODADA:
            break
        conta = post.account

        # Fila pausada pelo usuário: não publica nada dele.
        if post.owner.publishing_paused:
            continue

        # Fila nomeada pausada individualmente.
        if post.queue and post.queue.paused:
            continue

        # Conta banida pelo admin (moderação): não publica mais nada.
        if conta.banned_by_admin:
            continue

        # Conta pausada pelo usuário: para a fila DELA, o resto segue normal.
        if conta.pausada:
            continue

        # Conta caída que exige RECONEXÃO (sessão expirada / 2FA / challenge /
        # erro de token-app / banida): não publica até religar — evita martelar
        # com posts que só vão falhar (o 190 grava 'error'; sem incluí-lo aqui, a
        # conta redespachava a cada 2h e refazia a chamada falha, agravando a
        # punição da Meta). Volta sozinha quando o token/sessão for religado.
        if conta.status in ('session_expired', 'challenge_required', '2fa_required',
                            'error', 'banned'):
            continue

        if conta.id in despachadas:
            continue  # já mandamos um post desta conta nesta rodada

        # Modo forçado: o usuário assumiu o risco e mandou publicar mesmo
        # limitada. Pula cooldown e teto diário (a Meta ainda pode recusar).
        forcado = conta.ignorar_limites

        # INTERVALO MÍNIMO entre publicações da MESMA conta (anti-burst). É a
        # regra MAIS IMPORTANTE contra queda: publicar a sequência toda de uma
        # vez (segundos de diferença) o IG identifica como SPAM e DERRUBA a conta
        # (feedback real). Vale SEMPRE — inclusive com "Forçar". Forçar ignora só
        # o TETO DIÁRIO e o cooldown de rate-limit, NUNCA o espaçamento.
        from django.conf import settings as _cfg2
        gap_min = getattr(_cfg2, 'MIN_INTERVALO_POST_MIN', 40)
        # Post EM VOO (processing) conta como "acabou de postar": sem isto, o gap
        # baseado só em published_at furava (o post em voo ainda não publicou, e
        # a próxima rodada disparava outro → saíam com segundos de diferença).
        if ScheduledPost.objects.filter(account=conta, status='processing').exists():
            continue
        ultimo_pub = (ScheduledPost.objects
                      .filter(account=conta, status='published',
                              published_at__isnull=False)
                      .order_by('-published_at')
                      .values_list('published_at', flat=True).first())
        if ultimo_pub and (now - ultimo_pub) < timedelta(minutes=gap_min):
            continue

        # Conta em cooldown por rate limit: reagenda o post para o FIM do
        # cooldown (em vez de deixar vencido, martelando a cada rodada).
        if not forcado and conta.rate_limited_until and conta.rate_limited_until > now:
            if post.scheduled_for < conta.rate_limited_until:
                post.scheduled_for = conta.rate_limited_until
                post.save(update_fields=['scheduled_for'])
            continue

        # Teto efetivo = menor entre o limite do usuário e a cota real da Meta.
        # Ritmar pela cota real evita o corte de surpresa da Meta.
        limite = 0 if forcado else conta.teto_efetivo
        if limite > 0:
            publicados_24h = ScheduledPost.objects.filter(
                account=conta, status='published', published_at__gte=janela_24h
            ).count()
            if publicados_24h >= limite:
                # Reagenda para quando uma vaga da janela de 24h abrir — assim
                # o horário do post fica HONESTO e ele não fica "vencido"
                # reprocessando a cada minuto.
                quando = conta.livre_em() or (now + timedelta(hours=1))
                if post.scheduled_for < quando:
                    post.scheduled_for = quando
                    post.save(update_fields=['scheduled_for'])
                continue

        post.status = 'processing'
        post.processing_since = now
        post.save(update_fields=['status', 'processing_since'])
        publish_reel.delay(post.id)
        despachadas.add(conta.id)

        # Marca o rodízio: esta fila acabou de despachar.
        if post.queue:
            post.queue.last_dispatch = now
            post.queue.save(update_fields=['last_dispatch'])


def _e_rate_limit(msg):
    """A mensagem de erro indica limite de publicações da Meta?"""
    m = (msg or '').lower()
    return (
        'too many actions' in m
        or '2207042' in m
        or "'code': 9" in m
        or 'número máximo' in m
        or 'numero maximo' in m
        or 'application request limit' in m
        or 'rate limit' in m
    )


def _e_sessao_morta(msg):
    """A sessão da engine (instagrapi) caiu/foi deslogada — retry não resolve,
    a conta precisa reconectar. É diferente de app Meta inválido (Graph)."""
    m = (msg or '').lower()
    return (
        'user_has_logged_out' in m
        or 'logout_reason' in m
        or 'login_required' in m
        or 'loginrequired' in m
        or 'checkpoint_required' in m
        or 'challenge_required' in m
        or 'reconecte a conta pela aba' in m   # erro que a própria engine levanta
    )


def _e_app_invalido(msg):
    """Erro de TOKEN/APP inválido (não é rate limit, não adianta retry).

    O caso que derrubou tudo de madrugada: a Meta restringiu o app e passou a
    responder 'cannot access the app till you log in to www.instagram.com'.
    Cada retry é uma nova chamada a um app bloqueado — inútil e prejudicial
    (satura o worker e agrava a restrição). Aqui a gente reconhece e para.
    """
    m = (msg or '').lower()
    return (
        'cannot access the app' in m
        or 'error validating access token' in m
        or "'code': 190" in m
        or 'oauthexception' in m
        or 'session has been invalidated' in m
        or 'session has expired' in m
    )


# Erros em que vale REVEZAR de API (Graph <-> engine). São falhas LIMPAS, de
# autenticação/capacidade: a mídia NÃO foi publicada, então cair para a outra
# API não duplica o post. Timeout e erros ambíguos ficam de fora de propósito
# (poderiam ter publicado — revezar duplicaria).
def _deve_revezar(msg):
    m = (msg or '').lower()
    gatilhos = (
        'cannot access the app', 'error validating access token', 'invalid oauth access token',
        "'code': 190", 'oauthexception', 'session has been invalidated', 'session has expired',
        'unsupported request', 'application does not have permission', 'not authorized',
        'permissions error', 'login_required', 'loginrequired', 'challenge_required',
        # Mídia processada no braço não existe na URL pública do painel (404):
        # a Graph API não consegue baixá-la, mas a engine sobe os bytes locais.
        # Falha LIMPA (nada foi publicado), então revezar não duplica.
        'não está acessível publicamente', 'a meta precisa baixá-la',
        # ig_user_id inválido no Graph (o self-heal tenta corrigir antes; se
        # ainda falhar, a engine/sessão publica). Nada foi publicado -> sem duplicar.
        'does not exist', 'unsupported post request', 'cannot be loaded',
    )
    return any(g in m for g in gatilhos)


@shared_task
def publish_reel(post_id):
    """
    Tarefa que faz o upload real do vídeo para o Instagram.
    """
    try:
        post = ScheduledPost.objects.get(id=post_id)
    except ScheduledPost.DoesNotExist:
        print(f"Post {post_id} não existe mais; ignorando.")
        return

    # ── GUARDA ANTI-MARTELO (crítico) ─────────────────────────────────────────
    # NUNCA chama o Instagram para uma conta que já sabemos estar CAÍDA (sessão
    # expirada / challenge / 2FA / banida). Sem isto, um post preso numa conta
    # suspensa era redisparado e MARTELAVA o IG — visto em produção: o mesmo post
    # 240x em 2h pelo IP do braço. Esse padrão abusivo ajuda a Meta a flagar o
    # IP/app e invalidar TOKENS de outras contas. Deixa o post na fila e sai.
    _conta = post.account
    # Inclui 'error' (é o status que o handler de app inválido/190 grava) e
    # 'banned' — sem eles, passado o cooldown de 2h, o post era redespachado e
    # refazia a chamada Graph com token morto → novo 190 → loop a cada 2h.
    _bloqueada = (_conta.banned_by_admin
                  or _conta.status in ('session_expired', 'challenge_required',
                                       '2fa_required', 'banned', 'error'))
    # Só-sessão caída sem token não tem como publicar; conta com token ainda
    # publica pelo Graph, então não bloqueamos por 'sessao_expirada' aqui.
    if _bloqueada:
        if post.status != 'queued':
            post.status = 'queued'
            post.save(update_fields=['status'])
        print(f"Post {post_id}: @{_conta.ig_username} está {_conta.status} — NÃO publica (guarda anti-martelo).")
        return

    # Temporários baixados/gerados por esta execução. Pré-inicializados aqui para
    # o `finally` sempre poder limpá-los — mesmo que a exceção venha cedo (antes
    # de eles serem reatribuídos no corpo). Em falha/retry eram vazados em disco.
    fonte_baixada = arquivo_temporario = temp_audio = None
    audio_src_temp = thumb_temp = None

    try:
        engine = InstagramEngine(post.account)

        # O caption final pode ser a mistura do texto e hashtags, etc
        final_caption = post.caption
        if post.caption_set:
            # Pegar uma legenda do set (aqui poderíamos usar a com menor used_count)
            caption_obj = post.caption_set.captions.order_by('used_count').first()
            if caption_obj:
                final_caption = f"{final_caption}\n\n{caption_obj.text}\n\n{caption_obj.hashtags}"
                caption_obj.used_count += 1
                caption_obj.save()

        # Spintax: Processar variáveis dinâmicas na legenda
        hoje = timezone.now()
        dias_semana = ['Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira', 'Sábado', 'Domingo']
        
        spintax_map = {
            '{nome_conta}': post.account.ig_username,
            '{dia_semana}': dias_semana[hoje.weekday()],
            '{data_hoje}': hoje.strftime('%d/%m/%Y'),
        }
        
        for key, value in spintax_map.items():
            final_caption = final_caption.replace(key, value)

        # VARIAÇÃO AUTOMÁTICA por conta: o IG remove o texto de posts que
        # parecem coordenados (mesma legenda em massa). Aqui cada conta gera uma
        # versão única (spintax {a|b|c} + caracteres invisíveis), determinística
        # por conta+post (retry gera a MESMA legenda). Ligado por padrão.
        from django.conf import settings as _cfg_var
        if final_caption and getattr(_cfg_var, 'VARIAR_LEGENDAS', True):
            from apps.publisher.caption_utils import variar_legenda
            final_caption = variar_legenda(final_caption, seed=f"{post.account_id}-{post.id}")

        # Detecta imagem x vídeo pela extensão do arquivo.
        IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.webp')
        is_image = (post.video_file.name or '').lower().endswith(IMAGE_EXTS)

        # ── Limpeza / diversificação do arquivo ────────────────────────────
        # Cada conta publica um arquivo com hash (e, no ultra, fingerprint)
        # diferente, para o Instagram não correlacionar as contas.
        import os
        from django.conf import settings as dj_settings
        from apps.core_utils import garantir_midia_local

        # Garante o arquivo LOCAL. No painel/máquina única é no-op (já está no
        # disco); no braço (que não tem o volume de mídia) baixa da URL pública.
        publish_path, src_temp = garantir_midia_local(post.video_file)
        # Guarda o caminho do fonte baixado ANTES de publish_path ser trocado
        # por versões processadas (áudio/limpeza), para poder removê-lo no fim.
        fonte_baixada = publish_path if src_temp else None
        publish_relname = post.video_file.name
        arquivo_temporario = None
        temp_audio = None
        audio_src_temp = None
        thumb_path = None
        thumb_temp = None

        # ── Trilha da aba Áudios (substitui o som do vídeo) ────────────────
        # Feito ANTES da limpeza, para o fingerprint valer sobre o arquivo final.
        if post.audio_id and not is_image:
            from engine.media_cleaner import aplicar_audio
            audio_local, audio_src_temp = garantir_midia_local(post.audio.file)
            com_audio = aplicar_audio(
                publish_path,
                audio_local,
                dest_dir=os.path.join(dj_settings.MEDIA_ROOT, 'processed'),
            )
            if com_audio and com_audio != publish_path:
                publish_path = com_audio
                temp_audio = com_audio
                publish_relname = os.path.relpath(com_audio, dj_settings.MEDIA_ROOT).replace('\\', '/')
                post.audio.used_count += 1
                post.audio.save(update_fields=['used_count'])
                print(f"Post {post.id}: trilha '{post.audio.name}' aplicada")

        clean_mode = getattr(post, 'clean_mode', 'none') or 'none'
        if clean_mode != 'none' and not is_image:
            from engine.media_cleaner import limpar_video
            processado = limpar_video(
                publish_path,
                mode=clean_mode,
                # Seed por conta+mídia: mesma conta gera sempre o mesmo
                # tratamento, contas diferentes geram arquivos diferentes.
                seed=f"{post.account_id}-{post.video_file.name}",
                dest_dir=os.path.join(dj_settings.MEDIA_ROOT, 'processed'),
            )
            if processado and processado != publish_path:
                publish_path = processado
                arquivo_temporario = processado
                publish_relname = os.path.relpath(
                    processado, dj_settings.MEDIA_ROOT
                ).replace('\\', '/')
                print(f"Post {post.id}: mídia processada (modo={clean_mode})")

        # Story de IMAGEM com texto do editor visual: queima o texto na imagem
        # (na posição/cor/tamanho escolhidos) antes de subir. Best-effort: se
        # falhar, segue com a imagem original.
        if (post.post_type == 'STORY' and is_image
                and (getattr(post, 'story_text', '') or '').strip()):
            from engine.story_render import bake_story_text
            baked = bake_story_text(
                publish_path,
                text=post.story_text,
                color=post.story_text_color,
                bg=post.story_text_bg,
                size_preview=post.story_text_size,
                x=post.story_text_x, y=post.story_text_y,
                dest_dir=os.path.join(dj_settings.MEDIA_ROOT, 'processed'),
            )
            if baked and baked != publish_path:
                publish_path = baked
                arquivo_temporario = baked
                publish_relname = os.path.relpath(baked, dj_settings.MEDIA_ROOT).replace('\\', '/')
                print(f"Post {post.id}: texto do story queimado na imagem")

        # Posição da etiqueta de link no Story (x/y relativos do editor).
        link_pos = (getattr(post, 'story_link_x', 0.5), getattr(post, 'story_link_y', 0.82))
        story_link = (getattr(post, 'story_link', '') or '').strip()

        # Thumbnail local (a engine sobe o arquivo; no braço, baixa da URL).
        if post.thumbnail:
            thumb_path, thumb_temp = garantir_midia_local(post.thumbnail)

        # ── Publicação com REVEZAMENTO de API (Graph API <-> engine) ───────
        # Tenta a API principal; se ela falhar de forma LIMPA (auth/capacidade,
        # sem ter publicado), cai para a outra API disponível na conta. Assim,
        # um app Meta restrito (190) ou um "unsupported request" não derruba o
        # post quando a conta também tem sessão — e vice-versa.
        def _via_graph():
            from django.conf import settings as _s
            # SITE_URL precisa ser pública: a Meta baixa a mídia dessa URL.
            site_url = getattr(_s, 'SITE_URL', 'http://localhost:8000').rstrip('/')
            # Mídia processada (limpeza/story text) só existe no disco do braço.
            # Antes de a Meta tentar baixá-la pela URL do painel, enviamos o
            # arquivo pra lá — senão contas SÓ-Graph pegam 404. No painel/máquina
            # única é no-op. Original (reels/…) já está no painel: não reenvia.
            if publish_relname.startswith('processed/'):
                from apps.core_utils import enviar_midia_para_painel
                enviar_midia_para_painel(publish_path, publish_relname)
            from apps.core_utils import url_midia
            media_url = url_midia(site_url, dj_settings.MEDIA_URL, publish_relname)
            cover_url = f"{site_url}{post.thumbnail.url}" if post.thumbnail else None
            mi = engine.publish_meta_api(
                media_url=media_url, caption=final_caption, post_type=post.post_type,
                cover_url=cover_url, share_to_feed=post.share_to_feed, is_image=is_image,
            )
            return mi, str(mi.get('id', ''))

        def _via_engine():
            if post.post_type == 'STORY':
                mi = engine.upload_story(
                    publish_path, link_url=story_link or None, link_pos=link_pos,
                    link_label=(getattr(post, 'story_link_label', '') or 'CLIQUE AQUI'),
                )
                return mi, str(mi.get('pk') or mi.get('id') or '')
            mi = engine.upload_reel(
                video_path=publish_path, caption=final_caption,
                thumbnail_path=thumb_path,
            )
            return mi, str(mi.get('id', ''))

        tem_graph = bool(post.account.meta_access_token)
        tem_sessao = getattr(post.account, 'tem_sessao_engine', False)

        if post.post_type == 'STORY' and story_link:
            # Story com link SÓ existe pela engine (a API oficial não tem sticker de link).
            ordem = [('engine', _via_engine)]
        elif tem_graph and tem_sessao:
            # Tem as duas credenciais: Graph primeiro (oficial, não bloqueia), engine de reserva.
            ordem = [('Graph API', _via_graph), ('engine', _via_engine)]
        elif tem_graph:
            ordem = [('Graph API', _via_graph)]
        elif tem_sessao:
            ordem = [('engine', _via_engine)]
        else:
            raise Exception('Conta sem token Meta e sem sessão — reconecte a conta para publicar.')

        media_info = None
        ultimo_erro = None
        for i, (metodo, fn) in enumerate(ordem):
            try:
                print(f"Publicando {post.id} ({post.post_type}) via {metodo}...")
                media_info, mid = fn()
                post.ig_media_id = mid
                if i > 0:
                    print(f"Post {post.id}: publicado no REVEZAMENTO via {metodo} (a 1ª API falhou limpo).")
                break
            except Exception as e:
                ultimo_erro = e
                tem_proxima = i < len(ordem) - 1
                # Só reveza em falha LIMPA de auth/capacidade; erro ambíguo sobe
                # (o retry normal cuida, sem risco de post duplicado).
                if tem_proxima and _deve_revezar(str(e)):
                    print(f"Post {post.id}: {metodo} falhou limpo ({str(e)[:90]}); revezando para a próxima API...")
                    continue
                raise
        if media_info is None:
            raise ultimo_erro or Exception('Falha ao publicar em todas as APIs disponíveis')

        post.status = 'published'
        post.published_at = timezone.now()

        # Confirmar a grade custa +1 chamada à Meta por post — com centenas de
        # posts/dia num único app, isso engorda o volume que leva ao banimento
        # do app. Por padrão fica DESLIGADO (VERIFICAR_GRADE=False); a grade
        # quase sempre dá "sim" mesmo. Ligue só se precisar auditar.
        from django.conf import settings as _dj
        if (getattr(_dj, 'VERIFICAR_GRADE', False)
                and post.post_type != 'STORY' and post.ig_media_id
                and post.account.meta_access_token):
            try:
                post.na_grade = engine.midia_na_grade(post.ig_media_id)
                if post.share_to_feed and post.na_grade is False:
                    print(f"Post {post.id}: pedimos a grade, mas a Meta diz que NÃO foi.")
            except Exception:
                pass

        post.save()

        # Alerta de STORY publicado (opcional, ligado nas Configurações).
        # Anti-spam: 1 aviso por conta a cada hora, mesmo com vários stories.
        if post.post_type == 'STORY':
            try:
                from apps.notifications.alertas import alertar
                agora_st = timezone.now()
                alertar(
                    post.owner, 'story_publicado',
                    'Story publicado',
                    f'@{post.account.ig_username} publicou um story.',
                    chave=f'story_ok:{post.account_id}:{agora_st:%Y%m%d%H}',
                    nivel='success', account=post.account,
                )
            except Exception:
                pass

        # Publicou: a conta claramente não está mais em cooldown.
        if post.account.rate_limited_until:
            post.account.rate_limited_until = None
            post.account.save(update_fields=['rate_limited_until'])

    except Exception as e:
        msg = str(e)

        if _e_sessao_morta(msg):
            # Sessão da engine caiu/deslogou (ex.: user_has_logged_out,
            # logout_reason 9). Retry NÃO resolve. HÍBRIDO:
            #  - Conta COM token OAuth: NÃO derruba — segue publicando feed/reels/
            #    story-simples pelo Graph; só marca a sessão caída (story-link/
            #    aquecimento esperam recolar o cookie). Esse post falhou por exigir
            #    a sessão → marca 'failed' com mensagem clara (sem loop).
            #  - Conta SÓ-sessão: sem outra via → status=session_expired e o post
            #    volta pra fila (o dispatcher pula a conta até religar).
            conta = post.account
            conta.sessao_expirada = True
            tem_token = bool(conta.meta_access_token)
            if tem_token:
                campos = ['sessao_expirada', 'last_error']
                conta.last_error = ('Sessão do story-link caiu — recole o sessionid no '
                                    'card. O resto segue publicando pelo OAuth.')
                if conta.status == 'session_expired':   # corrige estado antigo
                    conta.status = 'active'
                    campos.append('status')
                conta.save(update_fields=campos)
                post.status = 'failed'
                post.error_message = ('Sessão expirada: recole o sessionid e reenvie este '
                                      'post (story-link/engine precisa de sessão).')
                post.save(update_fields=['status', 'error_message'])
                print(f"Post {post_id}: SESSAO MORTA; @{conta.ig_username} tem token -> conta segue ATIVA (só sessão caída)")
            else:
                conta.status = 'session_expired'
                conta.last_error = ('Sessão do Instagram caiu (deslogada). Reconecte '
                                    'pela aba "Sessão" — cole o cookie do IG de novo.')
                conta.save(update_fields=['status', 'last_error', 'sessao_expirada'])
                post.status = 'queued'
                post.error_message = 'Sessão expirada — reconecte a conta para retomar.'
                post.save(update_fields=['status', 'error_message'])
                print(f"Post {post_id}: SESSAO MORTA; @{conta.ig_username} -> session_expired (sem retry)")
            try:
                from apps.notifications.alertas import alertar
                agora = timezone.now()
                alertar(
                    post.owner, 'conta_caiu',
                    'Sessão expirada',
                    f'@{conta.ig_username}: a sessão do story-link caiu. Recole o sessionid '
                    + ('(o resto segue pelo OAuth).' if tem_token else 'para religar a conta.'),
                    chave=f'sess:{conta.id}:{agora:%Y%m%d%H}',
                    nivel='warning' if tem_token else 'error', account=conta,
                )
            except Exception:
                pass

        elif _e_app_invalido(msg):
            # App/token restringido pela Meta (ex.: 190 "cannot access the app").
            # Retry NÃO resolve e só piora — para de martelar: marca a conta como
            # caída, põe um cooldown longo e reagenda o post para depois dele.
            # Quando o app voltar (a sincronização confirma), a conta volta e a
            # fila retoma sozinha.
            cooldown = timezone.now() + timedelta(hours=2)
            conta = post.account
            conta.status = 'error'
            from apps.core_utils import msg_meta_amigavel
            conta.last_error = msg_meta_amigavel(msg)
            conta.rate_limited_until = cooldown
            conta.save(update_fields=['status', 'last_error', 'rate_limited_until'])
            post.status = 'queued'
            post.scheduled_for = cooldown
            post.error_message = ('App Meta indisponível/restringido — a conta precisa ser '
                                  'reconectada (entre em instagram.com e siga as instruções). '
                                  'Reagendado.')
            post.save()
            print(f"Post {post_id}: APP INVALIDO; @{conta.ig_username} -> erro, cooldown {cooldown}")
            # Avisa o dono (1x por conta a cada hora).
            try:
                from apps.notifications.alertas import alertar
                agora = timezone.now()
                alertar(
                    post.owner, 'conta_caiu',
                    'Conta desconectada',
                    f'@{conta.ig_username}: o app Meta está restringido/indisponível. '
                    'Entre em instagram.com e siga as instruções para religar.',
                    chave=f'appinv:{conta.id}:{agora:%Y%m%d%H}',
                    nivel='error', account=conta,
                )
            except Exception:
                pass

        elif _e_rate_limit(msg):
            # Rate limit da Meta: NÃO conta como retry (a conta simplesmente
            # atingiu o teto de 24h). Coloca a conta em cooldown e reagenda o
            # post — assim paramos de martelar a API imediatamente.
            cooldown = timezone.now() + timedelta(hours=3)
            post.account.rate_limited_until = cooldown
            post.account.save(update_fields=['rate_limited_until'])
            post.status = 'queued'
            post.scheduled_for = cooldown
            post.error_message = 'Limite de publicações da Meta atingido — reagendado após cooldown.'
            post.save()
            print(f"Post {post_id}: rate limit; @{post.account.ig_username} em cooldown até {cooldown}")

        elif post.retry_count < post.max_retries:
            # Erro transitório: espera antes de tentar de novo (não no mesmo minuto).
            post.retry_count += 1
            post.status = 'queued'
            post.scheduled_for = timezone.now() + timedelta(minutes=10)
            post.error_message = msg
            post.save()
            print(f"Error publishing post {post_id} (retry {post.retry_count}): {msg[:200]}")

        else:
            post.status = 'failed'
            post.error_message = msg
            post.save()
            print(f"Post {post_id} FALHOU em definitivo: {msg[:200]}")

            # Avisa o dono (se ele quiser) — sem repetir a cada post da mesma
            # conta: a chave inclui só a conta e a hora.
            try:
                from apps.notifications.alertas import alertar
                agora = timezone.now()
                alertar(
                    post.owner, 'falha_publicacao',
                    'Falha ao publicar',
                    f'@{post.account.ig_username}: {msg[:140]}',
                    chave=f'falha:{post.account_id}:{agora:%Y%m%d%H}',
                    nivel='error', account=post.account,
                )
            except Exception:
                pass

    finally:
        # Limpa as cópias temporárias em QUALQUER desfecho (sucesso, falha ou
        # retry). Antes só limpava no sucesso — em falha/retry os arquivos
        # baixados no braço (fonte/áudio/thumb) e os processados vazavam em disco
        # a cada tentativa. Em retry, a próxima execução rebaixa/reprocessa.
        for temporario in (arquivo_temporario, temp_audio, fonte_baixada,
                           audio_src_temp, thumb_temp):
            if temporario:
                try:
                    os.remove(temporario)
                except Exception:
                    pass


@shared_task
def process_agenda_semanal():
    """Beat: dispara os planos semanais recorrentes cujo dia/hora chegou, criando
    os posts reais (ScheduledPost) ESPAÇADOS entre as contas (anti-queda). Marca
    last_run no dia para não duplicar."""
    from .models import AgendaSemanal
    now = timezone.localtime()
    hoje = now.date()
    wd = now.weekday()          # 0=segunda .. 6=domingo
    base = timezone.now()
    total = 0

    for ag in AgendaSemanal.objects.filter(active=True).prefetch_related('accounts'):
        if wd not in ag.weekdays_list:
            continue
        if ag.last_run == hoje:          # já disparou hoje
            continue
        if now.time() < ag.hora:         # ainda não chegou a hora
            continue
        if not ag.video_file:
            continue

        contas = [c for c in ag.accounts.all() if not c.pausada and not c.banned_by_admin]
        # Story COM link só existe pela engine (sessão). Conta sem sessão falharia
        # em retries silenciosos — pula essas, igual ao bloqueio do Composer.
        if ag.post_type == 'STORY' and ag.story_link:
            contas = [c for c in contas if getattr(c, 'tem_sessao_engine', False)]
        espac = max(1, ag.espacamento_min)
        for i, acc in enumerate(contas):
            when = base + timedelta(minutes=i * espac)   # espaça 1 conta por vez
            post = ScheduledPost(
                owner=ag.owner, account=acc, post_type=ag.post_type,
                caption=ag.caption, share_to_feed=ag.share_to_feed,
                clean_mode=ag.clean_mode,
                story_link=ag.story_link if ag.post_type == 'STORY' else '',
                story_link_label=ag.story_link_label or 'CLIQUE AQUI',
                status='queued', scheduled_for=when,
            )
            post.video_file.name = ag.video_file.name    # reaproveita a mídia salva
            if ag.thumbnail:
                post.thumbnail.name = ag.thumbnail.name
            post.save()
            total += 1

        ag.last_run = hoje
        ag.save(update_fields=['last_run'])

    if total:
        print(f"Agenda semanal: {total} post(s) criados nesta rodada.")
    return total
