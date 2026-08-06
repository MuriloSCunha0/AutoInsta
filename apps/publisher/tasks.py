import logging
import os
from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from .models import ScheduledPost, PostLoop
from engine.client import InstagramEngine

logger_pub = logging.getLogger('apps.publisher')


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

def diagnosticar_erro(msg):
    """Traduz o erro de uma publicação numa explicação SIMPLES + o que fazer.

    Fala com o dono da conta em linguagem direta: o que aconteceu e o próximo
    passo, sem jargão. Reconhece tanto os erros crus da Meta (pelos mesmos
    classificadores que o publisher usa) QUANTO as mensagens que o próprio
    sistema grava em error_message — que na prática são a maioria (ex.: "o
    horário passou há mais de 6h"). Devolve:
        {categoria, cor, titulo, explicacao, acao}
    cor: 'danger' = precisa AGIR · 'warning' = temporário/atenção, costuma
    voltar sozinho · 'info' = o sistema corrige sozinho.
    """
    m = (msg or '')
    if not m.strip():
        return None
    # Compara SEM acento: o texto guardado pode vir com acento diferente ou até
    # corrompido por encoding — 'publicação' e 'publicacao' têm de casar igual.
    import unicodedata
    b = ''.join(c for c in unicodedata.normalize('NFKD', m.lower())
                if not unicodedata.combining(c))

    def d(categoria, cor, titulo, explicacao, acao):
        return dict(categoria=categoria, cor=cor, titulo=titulo,
                    explicacao=explicacao, acao=acao)

    # ── Post cancelado por atraso (a conta estava fora) — DE LONGE o mais comum
    if 'o horario passou ha mais de' in b or 'subir a fila antiga' in b:
        return d('expirado', 'warning',
                 'Post não publicado — a conta estava fora no horário',
                 'Na hora agendada, a conta estava desconectada, pausada ou '
                 'limitada. O post esperou tempo demais e foi cancelado para não '
                 'publicar conteúdo velho de uma vez só.',
                 'Deixe a conta conectada e ativa no horário dos posts. Para '
                 'publicar esse conteúdo, é só agendar de novo.')

    # ── Instagram pausou a conta por um tempo (integridade / code 25) ──
    if (_e_restricao_temporaria(m) or 'restringiu a publicacao desta conta' in b
            or 'nao e queda nem limite de cota' in b):
        return d('restricao', 'warning',
                 'Instagram pausou esta conta por um tempo',
                 'O Instagram segurou as publicações desta conta temporariamente. '
                 'Não é queda nem limite: a conta e o acesso continuam certos.',
                 'Não precisa fazer nada — ela volta a postar sozinha. Não force '
                 'nem reconecte agora, porque insistir só piora.')

    # ── Limite de publicações das últimas 24h ──
    if _e_rate_limit(m) or 'limite de publicacoes da meta atingido' in b:
        return d('limite', 'warning',
                 'Limite de publicações atingido (24h)',
                 'A conta bateu o teto de publicações do feed nas últimas 24 '
                 'horas. É temporário.',
                 'Espere liberar — os stories continuam saindo. Se acontece '
                 'muito, reduza o volume desta conta.')

    # ── Conta desconectada (token/app caiu, ou pediu verificação) ──
    if (_e_app_invalido(m) or 'precisa ser reconectada' in b
            or 'app meta indisponivel' in b):
        return d('token', 'danger',
                 'Conta desconectada — precisa reconectar',
                 'O acesso desta conta expirou ou o Instagram pediu uma '
                 'verificação. Enquanto isso, ela não consegue publicar.',
                 'Entre no instagram.com com essa conta, resolva o que aparecer '
                 'e reconecte a conta aqui no sistema.')

    # ── Sessão (sessionid) caída ──
    if _e_sessao_morta(m):
        return d('sessao', 'danger',
                 'Sessão da conta expirada',
                 'O login salvo (sessionid) caiu. O que depende dele — story com '
                 'link, aquecimento e editar perfil — para; o resto continua pelo '
                 'acesso oficial, se houver.',
                 'Recole o sessionid da conta na aba "Sessão".')

    # ── Legenda longa demais ──
    if 'caption was too long' in b or ('legenda' in b and 'longa' in b):
        return d('legenda', 'danger',
                 'Legenda longa demais',
                 'A legenda passou do limite do Instagram (cerca de 2.200 '
                 'caracteres), então o post foi recusado.',
                 'Encurte a legenda e publique de novo.')

    # ── Conta não é Profissional / publicação não permitida ──
    if 'unsupported request - method type' in b or 'method type: post' in b:
        return d('permissao', 'danger',
                 'Publicação não permitida para esta conta',
                 'O Instagram recusou publicar por esta conta. Quase sempre é '
                 'porque ela não está como conta Profissional (Comercial ou de '
                 'Criador de Conteúdo), ou perdeu a permissão.',
                 'No app do Instagram, deixe a conta como Profissional e reconecte '
                 'aqui no sistema.')

    # ── Instagram bloqueou o envio de mídia por ora (excesso de atividade) ──
    if 'restricted from uploading' in b:
        return d('upload', 'warning',
                 'Instagram bloqueou o envio por agora',
                 'O Instagram restringiu temporariamente o envio de mídia por '
                 'esta conta — normalmente por excesso de atividade.',
                 'Espere um pouco e reduza o ritmo desta conta. Costuma voltar '
                 'sozinho.')

    # ── Arquivo que o Instagram não conseguiu carregar ──
    if ('nao esta acessivel' in b or 'a meta precisa baixa' in b
            or 'rejeitou a midia' in b):
        return d('midia', 'danger',
                 'O Instagram não conseguiu carregar o vídeo/foto',
                 'O arquivo não ficou acessível para o Instagram baixar — link '
                 'quebrado, arquivo ainda processando, ou nome inválido.',
                 'O sistema tenta de novo por outro caminho. Se continuar, '
                 'reenvie a mídia.')

    # ── Instabilidade de rede / demora ao falar com o Instagram ──
    if ('max retries exceeded' in b or 'httpsconnectionpool' in b
            or 'newconnection' in b or 'timeout aguardando o processamento' in b):
        return d('rede', 'warning',
                 'Falha de conexão com o Instagram',
                 'A conexão com o Instagram falhou ou demorou demais na hora de '
                 'enviar. Quase sempre é passageiro.',
                 'O sistema tenta de novo sozinho. Se acontecer em muitas contas '
                 'ao mesmo tempo, avise o suporte.')

    # ── Referência interna desatualizada (o sistema conserta sozinho) ──
    if 'does not exist' in b or 'unsupported post request' in b:
        return d('id', 'info',
                 'Dado da conta desatualizado (corrige sozinho)',
                 'Uma referência interna da conta estava velha.',
                 'Nada a fazer — o sistema corrige e publica sozinho.')

    # ── Desconhecido: mostra o texto limpo para o suporte investigar ──
    from apps.core_utils import msg_meta_amigavel
    return d('outro', 'secondary',
             'Erro ainda não catalogado',
             msg_meta_amigavel(m),
             'Se repetir, veja o erro técnico abaixo e avise o suporte.')


@shared_task
def limpar_midia_processada():
    """Faxina de MEDIA_ROOT/processed — cópias TRANSITÓRIAS de publicação (mídia
    limpa/diversificada que o braço sobe ao painel p/ a Meta baixar, e temporários
    de download). Depois do post publicar/expirar, não servem mais. Sem isto o
    painel enchia ~30GB/dia (83GB em 05/08/2026) até estourar o disco e derrubar
    o deploy (git fetch: 'unpack-objects failed').

    Roda na fila 'celery' (default) = worker do PAINEL, onde o volume de mídia
    vive. Apaga só arquivos com mtime além de PROCESSED_TTL_HORAS (default 6h) —
    bem acima da vida de um post (MAX_ATRASO_POST_HORAS), então nunca toca mídia
    em voo. Nenhum ScheduledPost aponta video_file/thumbnail para processed/
    (é sempre reels/ ou media_library/), então é seguro.
    """
    import os
    import time
    from django.conf import settings as _s
    ttl_h = getattr(_s, 'PROCESSED_TTL_HORAS', 6)
    corte = time.time() - ttl_h * 3600
    base = os.path.join(_s.MEDIA_ROOT, 'processed')
    if not os.path.isdir(base):
        return {'apagados': 0, 'gb': 0}
    apagados = 0
    liberados = 0
    try:
        with os.scandir(base) as it:
            for entry in it:
                try:
                    if not entry.is_file():
                        continue
                    st = entry.stat()
                    if st.st_mtime < corte:
                        os.remove(entry.path)
                        apagados += 1
                        liberados += st.st_size
                except OSError:
                    continue
    except OSError:
        return {'apagados': apagados, 'gb': round(liberados / 1073741824, 2)}
    if apagados:
        print(f"limpar_midia_processada: apagou {apagados} arquivo(s), "
              f"{liberados / 1073741824:.2f} GB liberados (>{ttl_h}h).")
    return {'apagados': apagados, 'gb': round(liberados / 1073741824, 2)}


def _recomendar_limite(post, conta):
    """Recomenda ao usuário quando a conta está no limite — SEM reagendar.

    O bloqueio por limite/cooldown era para AVISAR, não para embaralhar a fila
    (feedback do usuário: os posts "pulavam" de horário sozinhos). Então NÃO
    mexemos no scheduled_for — o post fica no horário dele e sai quando a conta
    liberar. Só mandamos uma recomendação, no máximo 1 por conta por dia."""
    try:
        from apps.notifications.alertas import alertar
        from django.utils import timezone as _tz
        alertar(
            post.owner, 'limite_atingido',
            'Conta no limite de publicação',
            f'@{conta.ig_username} atingiu o limite de publicações do feed. '
            'Os reels aguardam a conta liberar sozinha — se quiser, reduza o '
            'volume desta conta. Stories continuam saindo normalmente.',
            chave=f'limite:{conta.id}:{_tz.localdate()}',
            nivel='warning', account=conta)
    except Exception:
        pass


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

    # EXPIRA posts MUITO atrasados: quando uma conta fica FORA (caída/pausada/
    # cooldown), os posts acumulam com horário no passado. Ao voltar (ex.: clicar
    # em Sincronizar), NÃO se deve "subir a fila antiga de uma vez" (rajada de
    # conteúdo velho = spam = derruba a conta — feedback do usuário). Posts
    # atrasados além do limite viram 'failed' (saem da fila ativa; conteúdo velho
    # não vale republicar). Isso também estabiliza os horários (para de remexer
    # a fila atrasada a cada rodada). Ajustável por env.
    from django.conf import settings as _cfg0
    max_atraso_h = getattr(_cfg0, 'MAX_ATRASO_POST_HORAS', 6)
    vencidos = ScheduledPost.objects.filter(
        status='queued', scheduled_for__lt=now - timedelta(hours=max_atraso_h))
    n_venc = vencidos.update(
        status='failed',
        error_message=(f'Não publicado: o horário passou há mais de {max_atraso_h}h '
                       '(a conta estava fora). Evitamos subir a fila antiga de uma '
                       'vez. Reenvie se ainda quiser publicar.'))
    if n_venc:
        print(f"Dispatcher: {n_venc} post(s) antigos (>{max_atraso_h}h) expirados — sem rajada de fila velha.")

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
    gap_min = getattr(_cfg, 'MIN_INTERVALO_POST_MIN', 40)

    # Ao reagendar posts de uma conta LIMITADA, NÃO colapsar todos no mesmo
    # instante. Bug relatado: o usuário agenda de 30 em 30 min, a conta fica
    # limitada (cooldown/teto) e a fila inteira era jogada para o MESMO horário
    # (rate_limited_until / livre_em) → "momentos muito próximos". Aqui cada post
    # da mesma conta é espaçado por gap_min a partir da base, preservando uma fila
    # legível e coerente com o anti-burst (nunca sai mais rápido que gap_min).
    reagenda_cursor = {}

    def _reagenda_espacado(post, base, gap):
        alvo = reagenda_cursor.get(post.account_id)
        if alvo is None or alvo < base:
            alvo = base
        reagenda_cursor[post.account_id] = alvo + timedelta(minutes=gap)
        if post.scheduled_for < alvo:
            post.scheduled_for = alvo
            post.save(update_fields=['scheduled_for'])

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

        # STORY é ISENTO de TODAS as travas de volume/cadência do feed. O limite
        # e o anti-rajada existem para os REELS (é o feed que o IG pune por
        # volume); story é separado — o Instagram deixa postar story mesmo numa
        # conta que bateu o limite de feed. Barrar story aqui (cooldown, teto OU
        # espaçamento entre reels) era excesso nosso (reclamação de usuário).
        eh_story = (post.post_type == 'STORY')

        # INTERVALO MÍNIMO entre publicações da MESMA conta (anti-burst). É a
        # regra MAIS IMPORTANTE contra queda: publicar a sequência toda de uma
        # vez (segundos de diferença) o IG identifica como SPAM e DERRUBA a conta
        # (feedback real). Vale SEMPRE — inclusive com "Forçar". Forçar ignora só
        # o TETO DIÁRIO e o cooldown de rate-limit, NUNCA o espaçamento.
        # Usa o INTERVALO QUE O USUÁRIO CONFIGUROU no post (ex.: 30 min); só cai
        # no padrão global quando o post não tem intervalo próprio.
        gap_ef = post.interval_minutes or gap_min
        # Post EM VOO (processing) conta como "acabou de postar": sem isto, o gap
        # baseado só em published_at furava (o post em voo ainda não publicou, e
        # a próxima rodada disparava outro → saíam com segundos de diferença).
        # Story não entra nessa conta: ele não é "rajada" de feed.
        if not eh_story and ScheduledPost.objects.filter(
                account=conta, status='processing').exclude(post_type='STORY').exists():
            continue
        # O anti-rajada olha só os REELS/feed publicados — um story recente não
        # deve travar um reel, nem o reel recente deve travar um story.
        ultimo_pub = None if eh_story else (
            ScheduledPost.objects
            .filter(account=conta, status='published', published_at__isnull=False)
            .exclude(post_type='STORY')
            .order_by('-published_at')
            .values_list('published_at', flat=True).first())
        # REGRA: o horário AGENDADO é a fonte da verdade. Um post no seu horário
        # sai na hora marcada — o espaçamento de 30 min já foi embutido quando a
        # campanha foi criada. Não empurramos um post que está no horário só
        # porque o anterior saiu alguns minutos atrasado. O intervalo só é
        # aplicado "arbitrariamente" quando é REAGENDAMENTO:
        #   - BACKLOG: post atrasado (conta ficou fora e voltou, ou Forçar
        #     drenando a fila) → reespaça 30/30 a partir da última publicação
        #     real, para não sair em rajada;
        #   - PISO anti-rajada real: nunca 2 publicações da mesma conta com menos
        #     de MIN_BURST_FLOOR_MIN de diferença de verdade (ex.: o post anterior
        #     saiu muito atrasado), mesmo que o horário agendado já tenha chegado.
        if ultimo_pub and (now - ultimo_pub) < timedelta(minutes=gap_ef):
            tol = timedelta(minutes=getattr(_cfg, 'TOLERANCIA_ATRASO_MIN', 10))
            piso = timedelta(minutes=getattr(_cfg, 'MIN_BURST_FLOOR_MIN', 10))
            atrasado = (now - post.scheduled_for) > tol
            if atrasado or (now - ultimo_pub) < piso:
                _reagenda_espacado(post, ultimo_pub + timedelta(minutes=gap_ef), gap_ef)
                continue
            # senão: está no horário agendado e sem rajada real → publica na hora.

        # Conta em cooldown por rate limit: NÃO reagenda (era o pedido — a fila
        # não deve pular de horário sozinha). Deixa o post no lugar, pula esta
        # rodada e recomenda ao usuário. Story passa mesmo com a conta limitada.
        if not forcado and not eh_story and conta.rate_limited_until and conta.rate_limited_until > now:
            _recomendar_limite(post, conta)
            continue

        # Teto diário = menor entre o limite do usuário e a cota real da Meta.
        # Story NÃO conta para o teto e não é barrado por ele (limite é de feed).
        limite = 0 if (forcado or eh_story) else conta.teto_efetivo
        if limite > 0:
            publicados_24h = (ScheduledPost.objects
                              .filter(account=conta, status='published',
                                      published_at__gte=janela_24h)
                              .exclude(post_type='STORY').count())
            if publicados_24h >= limite:
                # NÃO reagenda: recomenda ao usuário e mantém o post no horário
                # dele. Sai quando uma vaga da janela de 24h abrir sozinha.
                _recomendar_limite(post, conta)
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


def _tem_code(msg, code):
    """A mensagem da Meta carrega este `code`?

    O erro chega como texto (str(exception)) e a serialização varia conforme o
    caminho — dict do requests, JSON cru ou a forma curta que a própria Meta usa
    no `message`. Cobrimos todas para não depender do formato:
        {'code': 190}   {"code": 190}   (#190)   code 190   code: 190
    """
    m = (msg or '').lower()
    return any(p in m for p in (
        f"'code': {code}", f'"code": {code}', f'(#{code})',
        f'code {code}', f'code: {code}',
    ))


def _e_rate_limit(msg):
    """A mensagem de erro indica LIMITE de publicação da Meta (temporário)?

    ATENÇÃO: a Meta devolve os erros de limite com `"type": "OAuthException"`,
    o MESMO tipo de um token inválido. Só o `code` separa os dois:

        code 4   — Application request limit reached
        code 9   — usuário atingiu o número máximo de publicações
        code 17  — User request limit reached
        code 32  — Page request limit reached
        code 613 — Calls to this api have exceeded the rate limit
        code 190 — token realmente inválido  <- ESTE não é limite

    Por isso `_e_app_invalido` NÃO pode casar com 'oauthexception' solto, e esta
    função é avaliada ANTES dela. Era o bug relatado pelo usuário iorio: contas
    perfeitamente saudáveis (token respondendo 200, cota 50/100) apareciam como
    "conta caiu — veja se está SUSPENSA", travando a fila sem necessidade.
    """
    m = (msg or '').lower()
    return (
        'too many actions' in m
        or '2207042' in m
        or 'número máximo' in m
        or 'numero maximo' in m
        or 'maximum number of' in m
        or 'application request limit' in m
        or 'user request limit' in m
        or 'page request limit' in m
        or 'exceeded the rate limit' in m
        or 'rate limit' in m
        or 'limit reached' in m
        # Códigos de limite da Meta (vêm como OAuthException, igual ao 190).
        or any(_tem_code(msg, c) for c in (4, 9, 17, 32, 613))
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

    NÃO casar com 'oauthexception' solto: a Meta usa esse mesmo tipo para os
    erros de LIMITE (codes 4/9/17/32/613), que são temporários e não exigem
    reconectar nada. Marcar uma conta saudável como caída trava a fila dela e
    mostra "veja se a conta está SUSPENSA" sem motivo (bug relatado pelo
    usuário iorio: 3 contas em 'error' cujo token respondia 200 na Graph API).
    Só assinaturas EXCLUSIVAS de token/app inválido entram aqui.
    """
    m = (msg or '').lower()
    if _e_rate_limit(msg):
        return False           # limite temporário nunca é token inválido
    return (
        'cannot access the app' in m
        or 'error validating access token' in m
        or 'invalid oauth access token' in m
        or _tem_code(msg, 190)
        or 'session has been invalidated' in m
        or 'session has expired' in m
    )


def _e_restricao_temporaria(msg):
    """A conta está com a publicação RESTRINGIDA temporariamente pela Meta.

        code 25 / error_subcode 2207050 — "User access is restricted"

    É diferente de tudo o que já tratamos: o TOKEN está bom (o /me responde 200)
    e a COTA não estourou — é a própria conta que o Instagram segurou por um
    tempo (integridade/comportamento). Verificado em produção nas contas do
    usuário iorio: a conta aparecia 'active' (o token valida), mas o post falhava
    com este erro, então o dono via "on" e "não posta" ao mesmo tempo, mais um
    alerta cru e assustador com o JSON da Meta.

    Retry imediato NÃO resolve e ainda insiste numa conta que a Meta pediu para
    deixar quieta — o mesmo padrão que agrava restrição. Aqui reconhecemos,
    damos um cooldown e explicamos.
    """
    m = (msg or '').lower()
    return (
        'user access is restricted' in m
        or '2207050' in m
        or _tem_code(msg, 25)
    )


# Erros em que vale REVEZAR de API (Graph <-> engine). São falhas LIMPAS, de
# autenticação/capacidade: a mídia NÃO foi publicada, então cair para a outra
# API não duplica o post. Timeout e erros ambíguos ficam de fora de propósito
# (poderiam ter publicado — revezar duplicaria).
def _deve_revezar(msg):
    m = (msg or '').lower()
    # Restrição da CONTA (code 25) não é problema da VIA: a conta está segurada
    # nas duas pontas, então revezar para a engine só bate na mesma parede (e
    # insiste numa conta que a Meta pediu para deixar quieta). Trata direto.
    if _e_restricao_temporaria(msg):
        return False
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
            from apps.publisher.caption_utils import variar_legenda, _so_invisivel
            # A variação semântica (sinônimos/emoji/saudação) só entra se a
            # campanha pediu (post.variar_auto); o spintax {a|b|c} vale sempre.
            antes = final_caption
            final_caption = variar_legenda(
                antes, seed=f"{post.account_id}-{post.id}",
                semantica=bool(getattr(post, 'variar_auto', True)))
            # 2ª rede (a 1ª está dentro de variar_legenda): publicar sem texto
            # uma campanha que TINHA texto é sempre erro nosso. Se algum dia a
            # variação zerar de novo, o post sai com a legenda original em vez
            # de sair mudo — e o log denuncia para a gente corrigir.
            if _so_invisivel(final_caption):
                logger_pub.error(
                    'Post %s (@%s): a variação zerou a legenda — publicando o '
                    'texto original. Legenda de entrada: %r',
                    post.id, post.account.ig_username, antes[:120])
                final_caption = antes

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
        # 'light' foi descontinuado (ver ScheduledPost.CLEAN_CHOICES): ele só
        # trocava o MD5 e carimbava `encoder=Lavf...` + `comment=<hex>` no MP4,
        # o que ligava as contas entre si. Normalizamos aqui — e não por
        # migração de dados — para que os posts JÁ agendados com 'light'
        # publiquem o arquivo original, sem reescrever a fila do usuário.
        if clean_mode == 'light':
            clean_mode = 'none'
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

        # Publicou: a conta claramente não está mais em cooldown nem "de molho".
        campos_ok = []
        if post.account.rate_limited_until:
            post.account.rate_limited_until = None
            campos_ok.append('rate_limited_until')
        if post.account.meta_limit_count:
            post.account.meta_limit_count = 0
            campos_ok.append('meta_limit_count')
        if campos_ok:
            post.account.save(update_fields=campos_ok)

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

        # Este ramo vem antes do de rate limit, mas `_e_app_invalido` já devolve
        # False quando a mensagem é de LIMITE — a Meta manda os dois como
        # OAuthException, e sem essa checagem toda conta que só bateu no limite
        # caía aqui e aparecia como "caiu / pode estar SUSPENSA", com a fila
        # travada à toa (bug do usuário iorio).
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
            # Rate limit da Meta: NÃO conta como retry (a conta atingiu o teto de
            # 24h). Conta quantas vezes SEGUIDAS a Meta limitou (zera ao publicar
            # com sucesso):
            #  - 1ª vez: cooldown CURTO (3h). Passado o cooldown, a conta tenta de
            #    novo SOZINHA.
            #  - 2ª vez+: DE MOLHO — cooldown LONGO (descansa ~até amanhã) e
            #    reagenda a fila. Também volta a tentar sozinha quando o cooldown
            #    passar; se limitar de novo, descansa outro período. NUNCA fica
            #    parada para sempre (antes virava `pausada=True` eterno).
            from django.conf import settings as _cfgrl
            conta = post.account
            # Se o usuário estava FORÇANDO e MESMO ASSIM a Meta limitou, desliga o
            # forçar desta conta — parar de martelar (era o pedido). Ele pode
            # religar por conta e risco, e aí avisamos que pode derrubar a conta.
            forcava = conta.ignorar_limites
            campos_conta = ['rate_limited_until', 'meta_limit_count']
            if forcava:
                conta.ignorar_limites = False
                campos_conta.append('ignorar_limites')
            conta.meta_limit_count = (conta.meta_limit_count or 0) + 1
            de_molho = conta.meta_limit_count >= 2
            horas = getattr(_cfgrl, 'DE_MOLHO_HORAS', 12) if de_molho else 3
            cooldown = timezone.now() + timedelta(hours=horas)
            conta.rate_limited_until = cooldown
            conta.save(update_fields=campos_conta)
            if de_molho:
                # ESTE post sai de 'processing' ANTES de reagendar a fila (senão
                # ficava "publicando..." para sempre e só voltava pela rede de
                # segurança de 15 min).
                post.status = 'queued'
                post.processing_since = None
                post.error_message = ('Conta de molho: a Meta limitou 2x seguidas. '
                                      'Descansando — volta a tentar sozinha quando o cooldown '
                                      'passar (fila reagendada).')
                post.save(update_fields=['status', 'processing_since', 'error_message'])
                movidos = conta.reagendar_fila_amanha()
                print(f"Post {post_id}: rate limit {conta.meta_limit_count}x; @{conta.ig_username} "
                      f"DE MOLHO até {cooldown} (volta sozinha), {movidos} post(s) reagendados.")
            else:
                post.status = 'queued'
                post.scheduled_for = cooldown
                post.error_message = ('Limite de publicações da Meta atingido — reagendado após '
                                      'cooldown. Volta a tentar sozinha quando o cooldown passar.')
                post.save()
                print(f"Post {post_id}: rate limit; @{conta.ig_username} em cooldown até {cooldown}")
            # Avisa o dono (1x por conta a cada hora).
            try:
                from apps.notifications.alertas import alertar
                agora = timezone.now()
                if de_molho:
                    titulo, corpo = (
                        'Conta de molho',
                        f'@{conta.ig_username}: a Meta limitou 2x seguidas. Botei de molho — '
                        'ela descansa e volta a tentar publicar sozinha quando o cooldown '
                        'passar (reagendei a fila). Não está travada.')
                else:
                    titulo, corpo = (
                        'Conta limitada pela Meta',
                        f'@{conta.ig_username}: a Meta limitou as publicações (volta a tentar '
                        f'sozinha {timezone.localtime(cooldown):%d/%m %H:%M}).')
                if forcava:
                    corpo += (' Você estava FORÇANDO esta conta — desliguei o forçar porque a '
                              'Meta limitou mesmo assim. Se forçar de novo, ela posta ignorando '
                              'o limite, com risco de DERRUBAR a conta.')
                alertar(post.owner, 'conta_caiu', titulo, corpo,
                        chave=f'ratelimit:{conta.id}:{agora:%Y%m%d%H}',
                        nivel='warning', account=conta)
            except Exception:
                pass

        elif _e_restricao_temporaria(msg):
            # Conta restringida temporariamente pela Meta (code 25 / 2207050).
            # O token está bom e a cota não estourou — é a conta que o IG segurou
            # por comportamento/integridade. Insistir agora só piora. Damos um
            # cooldown (3h), reagendamos o post para depois dele e NÃO gastamos
            # retry. O cooldown faz a conta aparecer como "limitada" na Gestão
            # (em vez de "on" sem publicar), e o alerta explica em vez de mostrar
            # o JSON cru. A conta NÃO vira 'error': o token continua válido e ela
            # volta sozinha quando a Meta soltar.
            conta = post.account
            cooldown = timezone.now() + timedelta(hours=3)
            campos = []
            if not conta.rate_limited_until or conta.rate_limited_until < cooldown:
                conta.rate_limited_until = cooldown
                campos.append('rate_limited_until')
            conta.last_error = ('Publicação temporariamente restringida pela Meta '
                                '(a conta está OK — nada de reconectar). Volta '
                                'sozinha quando a restrição sair.')
            campos.append('last_error')
            conta.save(update_fields=campos)
            post.status = 'queued'
            post.scheduled_for = cooldown
            post.error_message = ('Meta restringiu a publicação desta conta por um '
                                  'tempo (não é queda nem limite de cota). '
                                  'Reagendado — volta sozinho.')
            post.save(update_fields=['status', 'scheduled_for', 'error_message'])
            print(f"Post {post_id}: RESTRICAO TEMP (code 25); @{conta.ig_username} "
                  f"cooldown {cooldown}")
            try:
                from apps.notifications.alertas import alertar
                agora = timezone.now()
                alertar(
                    post.owner, 'conta_caiu',
                    'Publicação restringida',
                    f'@{conta.ig_username}: a Meta restringiu a publicação desta '
                    'conta por um tempo. A conta está OK (não precisa reconectar) '
                    'e volta a postar sozinha quando a restrição sair.',
                    chave=f'restr25:{conta.id}:{agora:%Y%m%d}',
                    nivel='warning', account=conta)
            except Exception:
                pass

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
