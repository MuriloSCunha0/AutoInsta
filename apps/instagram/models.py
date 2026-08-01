from django.db import models
from apps.accounts.models import User
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _get_fernet():
    """Retorna um Fernet válido ou levanta um erro claro de configuração."""
    key = (settings.FERNET_KEY or "").strip()
    try:
        return Fernet(key.encode())
    except Exception:
        raise ImproperlyConfigured(
            "FERNET_KEY ausente ou inválida. Gere uma com "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` e defina a variável "
            "de ambiente FERNET_KEY no Railway."
        )

class Pasta(models.Model):
    """Pasta para organizar contas num nível acima do 'modelo'.

    Ex.: pasta "Clientes premium" agrupando várias modelos. O modelo continua
    sendo a subdivisão dentro da pasta.
    """
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pastas')
    name = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        unique_together = ['owner', 'name']

    def __str__(self):
        return self.name


class InstagramAccount(models.Model):
    STATUS_CHOICES = [
        ('connecting', 'Conectando...'),
        ('active', 'Ativa ✅'),
        ('challenge_required', 'Código necessário 🔑'),
        ('2fa_required', '2FA necessário 🔐'),
        ('session_expired', 'Sessão expirada 🕒'),
        ('banned', 'Banida/indisponível 🚫'),
        ('error', 'Erro ❌'),
    ]

    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    # App Meta pelo qual esta conta foi conectada. Cada conta pertence ao app
    # que gerou seu token — por isso o vínculo é por conta, não global.
    meta_app = models.ForeignKey(
        'accounts.MetaApp', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='accounts',
    )
    ig_username = models.CharField(max_length=150)
    # Pasta (organização de alto nível, acima do "modelo"). Opcional.
    pasta = models.ForeignKey(
        Pasta, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='contas',
    )
    # "Modelo" (ex.: op1): agrupa contas de uma mesma modelo/operação, para
    # separar visualmente no painel e no composer. Vazio = "Sem modelo".
    modelo = models.CharField(max_length=60, blank=True, db_index=True)
    ig_password = models.TextField()
    # Seed do 2FA (TOTP), criptografado. Com ele, o login gera o código de 6
    # dígitos SOZINHO — sem digitação — e passa do 2FA a partir da VPS (o IG
    # aceita login por senha quando a conta tem 2FA). Vazio = sem 2FA salvo.
    totp_seed_enc = models.TextField(blank=True, default='')
    proxy_url = models.CharField(max_length=255, blank=True, help_text="Ex: http://user:pass@ip:port")
    ig_user_id = models.BigIntegerField(null=True, blank=True)
    # URLs de foto do CDN do Instagram passam de 300-800 chars; o default do
    # URLField (200) estourava com "value too long for type character varying(200)".
    profile_pic_url = models.URLField(max_length=1000, blank=True)
    full_name = models.CharField(max_length=255, blank=True)
    bio = models.TextField(blank=True)
    followers_count = models.IntegerField(default=0)
    following_count = models.IntegerField(default=0)
    posts_count = models.IntegerField(default=0)

    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='connecting')
    # Teto de publicações por dia nesta conta (0 = sem limite). Ajuda a evitar
    # bloqueios por volume — o Composer distribui o excedente para os dias
    # seguintes quando o modo "Respeitar limite" está ligado.
    daily_post_limit = models.IntegerField(default=100)
    # Quando a Meta sinaliza rate limit, a conta fica em espera até este horário.
    # Enquanto isso, a fila NÃO tenta publicar nela (evita martelar a API — o
    # que é o padrão que dispara bans).
    rate_limited_until = models.DateTimeField(null=True, blank=True)
    # Modo forçado: ignora o teto diário e o cooldown de rate limit desta conta.
    # É o usuário assumindo o risco — a Meta ainda pode recusar por volume real.
    ignorar_limites = models.BooleanField(default=False)
    # Conta pausada pelo usuário: a fila DELA para, mas a das outras contas
    # continua normalmente. Serve para "desativar" uma conta problemática sem
    # travar o resto (ex.: enquanto ela está limitada).
    pausada = models.BooleanField(default=False)
    # Cota real de publicação da Meta (endpoint content_publishing_limit),
    # janela móvel de 24h. Preenchida na sincronização.
    quota_usage = models.IntegerField(default=0)
    quota_total = models.IntegerField(default=0)
    quota_checked_at = models.DateTimeField(null=True, blank=True)
    # Visualizações reais da Meta (endpoint /insights, métrica `views`).
    # `views_today` = dia corrente; `views_total` = tudo que a Meta ainda
    # guarda (ela mantém no máximo 2 anos de insights).
    views_today = models.IntegerField(default=0)
    views_total = models.IntegerField(default=0)
    views_checked_at = models.DateTimeField(null=True, blank=True)
    # Moderação: banimento manual pelo admin (independe do status da Meta).
    # Quando True, a conta não publica mais — usado quando o admin revisa o
    # conteúdo e decide barrar. Silencioso: o usuário não é notificado.
    banned_by_admin = models.BooleanField(default=False)
    banned_reason = models.CharField(max_length=255, blank=True)
    banned_at = models.DateTimeField(null=True, blank=True)
    session_blob = models.JSONField(null=True, blank=True)
    # HÍBRIDO: a saúde da SESSÃO (engine/sessionid) é rastreada à parte do
    # `status` geral. Assim, numa conta que TAMBÉM tem token OAuth, a sessão pode
    # cair (story-link/aquecimento ficam indisponíveis) SEM derrubar a conta — ela
    # segue publicando feed/reels/story-simples pela API oficial. Só vira
    # status=session_expired quando a conta NÃO tem token (não há outra via).
    sessao_expirada = models.BooleanField(default=False)
    meta_access_token = models.TextField(blank=True, help_text="Token da API Oficial (Meta Graph)")
    # Quando o long-lived token da API oficial vence (~60 dias). O beat renova
    # ANTES disso (refresh_access_token) — token vencido é a causa do acúmulo de
    # contas só-token em "erro". Null = validade desconhecida (legado/colado).
    meta_token_expira_em = models.DateTimeField(null=True, blank=True)
    device_settings = models.JSONField(null=True, blank=True)
    challenge_type = models.CharField(max_length=50, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    last_action_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['owner', 'ig_username']

    def __str__(self):
        """Exibido nos selects de formulário (antes saía 'InstagramAccount object (12)')."""
        name = f"@{self.ig_username}" if self.ig_username else f"conta #{self.pk}"
        return f"{name} — {self.full_name}" if self.full_name else name

    def set_ig_password(self, raw_password):
        self.ig_password = _get_fernet().encrypt(raw_password.encode()).decode()

    def get_ig_password(self):
        return _get_fernet().decrypt(self.ig_password.encode()).decode()

    def set_totp_seed(self, raw_seed):
        """Guarda o seed do 2FA (TOTP), criptografado. Normaliza (tira espaços
        e deixa maiúsculo — o IG mostra em grupos tipo 'ABCD EFGH ...')."""
        seed = (raw_seed or '').replace(' ', '').strip().upper()
        self.totp_seed_enc = _get_fernet().encrypt(seed.encode()).decode() if seed else ''

    def get_totp_seed(self):
        if not self.totp_seed_enc:
            return ''
        try:
            return _get_fernet().decrypt(self.totp_seed_enc.encode()).decode()
        except Exception:
            return ''

    def set_meta_token(self, raw_token):
        """Criptografa e guarda o token da Meta Graph API (mesmo cofre da senha)."""
        self.meta_access_token = (
            _get_fernet().encrypt(raw_token.encode()).decode() if raw_token else ''
        )

    def set_meta_token_expiry(self, expires_in):
        """Grava quando o long-lived token vence, a partir do `expires_in` (seg)
        que a Meta devolve na troca/renovação. Ignora valor inválido."""
        from django.utils import timezone
        from datetime import timedelta
        try:
            secs = int(expires_in)
        except (TypeError, ValueError):
            return
        if secs > 0:
            self.meta_token_expira_em = timezone.now() + timedelta(seconds=secs)

    def get_meta_token(self):
        """Token Meta em texto puro. Tolera tokens legados salvos sem criptografia."""
        stored = self.meta_access_token or ''
        if not stored:
            return ''
        try:
            return _get_fernet().decrypt(stored.encode()).decode()
        except Exception:
            # Token gravado antes da criptografia: devolve como está (retrocompat).
            return stored

    @property
    def is_active(self):
        return self.status == 'active'

    @property
    def token_vencido(self):
        """Token da API oficial já venceu (precisa colar um novo / reconectar)."""
        from django.utils import timezone
        return bool(self.meta_access_token and self.meta_token_expira_em
                    and self.meta_token_expira_em <= timezone.now())

    @property
    def token_expira_em_breve(self):
        """Token vence nos próximos 10 dias — avisar para renovar a tempo."""
        from django.utils import timezone
        from datetime import timedelta
        if not (self.meta_access_token and self.meta_token_expira_em):
            return False
        agora = timezone.now()
        return agora < self.meta_token_expira_em <= agora + timedelta(days=10)

    @property
    def em_cooldown(self):
        """Conta em espera por rate limit da Meta neste momento."""
        from django.utils import timezone
        return bool(self.rate_limited_until and self.rate_limited_until > timezone.now())

    def credenciais_meta(self):
        """(app_id, app_secret) do app DESTA conta. Nunca de outro.

        Cada conta é única e pertence ao Meta app pelo qual foi conectada.
        Não existe fallback para "app ativo do usuário" nem para credencial
        global: usar o app errado gera token inválido e derruba a conta.
        """
        app = self.meta_app
        if not app:
            raise ValueError(
                f'@{self.ig_username} não está vinculada a um app Meta. '
                'Reconecte a conta escolhendo o app dela.'
            )
        if app.owner_id != self.owner_id:
            raise ValueError(
                f'@{self.ig_username} está vinculada a um app de outro dono.'
            )
        return (app.meta_app_id or '').strip(), app.get_meta_secret()

    @property
    def teto_efetivo(self):
        """Máximo de posts em 24h — o MENOR entre o limite do usuário e a cota
        real da Meta (content_publishing_limit, ~100). 0 = sem teto.

        Ritmar pela cota real evita o pior cenário: o usuário põe 500, a conta
        vai a todo vapor e a Meta CORTA de surpresa com 3h de cooldown. Com o
        teto real, a conta desliza logo abaixo do limite e nunca leva o corte.
        """
        tetos = [t for t in (self.daily_post_limit or 0, self.quota_total or 0) if t > 0]
        return min(tetos) if tetos else 0

    def _publicados_24h(self):
        from datetime import timedelta

        from django.utils import timezone
        from apps.publisher.models import ScheduledPost
        return ScheduledPost.objects.filter(
            account=self, status='published',
            published_at__gte=timezone.now() - timedelta(hours=24),
        )

    @property
    def esta_limitada(self):
        """Está barrada agora — por cooldown da Meta ou pelo teto efetivo."""
        if self.em_cooldown:
            return True
        teto = self.teto_efetivo
        if teto <= 0:
            return False
        return self._publicados_24h().count() >= teto

    @property
    def proximo_post(self):
        """O PRÓXIMO post a sair desta conta (o mais próximo no futuro/vencido),
        não o último. Usado no card para mostrar quando sai o próximo."""
        from apps.publisher.models import ScheduledPost
        return (ScheduledPost.objects.filter(
            account=self, status__in=('queued', 'processing'))
            .order_by('scheduled_for').first())

    def livre_em(self):
        """Quando a conta volta a poder publicar (datetime), ou None se livre.

        - Em cooldown da Meta: até o fim do cooldown.
        - No teto de 24h: quando o post mais antigo da janela sai dos 24h,
          liberando uma vaga (janela móvel).
        """
        from datetime import timedelta

        from django.utils import timezone
        agora = timezone.now()
        if self.em_cooldown:
            return self.rate_limited_until
        teto = self.teto_efetivo
        if teto <= 0:
            return None
        recentes = list(self._publicados_24h().order_by('published_at')
                        .values_list('published_at', flat=True))
        if len(recentes) < teto:
            return None
        # A vaga abre quando o mais antigo completa 24h.
        return recentes[0] + timedelta(hours=24)

    @property
    def tem_sessao_engine(self):
        """A engine (instagrapi) precisa de sessão salva ou senha utilizável.

        Contas conectadas SÓ por token da Meta não têm isso — e, por isso,
        não conseguem usar recursos que a API oficial não expõe (aquecimento,
        edição de bio/foto, Story com link).
        """
        if self.session_blob and not self.sessao_expirada:
            return True
        try:
            return self.get_ig_password() not in ('', '__session_login__')
        except Exception:
            return False

class WarmupConfig(models.Model):
    """Configuração de aquecimento (warm-up) por conta — ações sociais graduais
    para maturar contas novas. Só possível pela engine cinza (a API oficial não
    expõe likes/follows/views), o que é um diferencial sobre soluções API-only."""
    INTENSITY_CHOICES = [
        ('low', 'Leve'),
        ('medium', 'Moderado'),
        ('high', 'Agressivo'),
    ]
    # Alvos diários por intensidade: (likes, follows, views)
    INTENSITY_TARGETS = {
        'low': (10, 2, 20),
        'medium': (25, 5, 50),
        'high': (50, 10, 100),
    }

    account = models.OneToOneField(InstagramAccount, on_delete=models.CASCADE, related_name='warmup')
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False)
    intensity = models.CharField(max_length=10, choices=INTENSITY_CHOICES, default='low')
    target_hashtag = models.CharField(max_length=100, default='reels')

    # Contadores do dia (resetam quando counter_date muda)
    counter_date = models.DateField(null=True, blank=True)
    likes_today = models.IntegerField(default=0)
    follows_today = models.IntegerField(default=0)
    views_today = models.IntegerField(default=0)

    last_run = models.DateTimeField(null=True, blank=True)
    last_result = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def daily_targets(self):
        return self.INTENSITY_TARGETS.get(self.intensity, self.INTENSITY_TARGETS['low'])


class Proxy(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    ip_address = models.CharField(max_length=50)
    port = models.IntegerField()
    username = models.CharField(max_length=150, blank=True)
    password = models.CharField(max_length=150, blank=True)
    protocol = models.CharField(max_length=20, default='http')
    is_active = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.protocol}://{self.ip_address}:{self.port}"
        
    @property
    def url(self):
        if self.username and self.password:
            return f"{self.protocol}://{self.username}:{self.password}@{self.ip_address}:{self.port}"
        return f"{self.protocol}://{self.ip_address}:{self.port}"
