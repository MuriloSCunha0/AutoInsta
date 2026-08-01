"""
Configurações do Django para o projeto AutoInsta.

Plataforma SaaS multi-tenant de automação para Instagram.
"""
import os
from pathlib import Path

import environ
from celery.schedules import crontab

# Diretório base do projeto
BASE_DIR = Path(__file__).resolve().parent.parent

# Inicializar django-environ
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CSRF_TRUSTED_ORIGINS=(list, ["https://*.up.railway.app", "https://web-production-51afd.up.railway.app"]),
)

# Ler arquivo .env se existir
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

# =============================================================================
# Configurações Gerais
# =============================================================================
SECRET_KEY = env("SECRET_KEY", default="dev-insecure-change-me-in-production")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = ['*'] # Accept all hosts for now on Railway
CSRF_TRUSTED_ORIGINS = ['https://*.up.railway.app', 'https://web-production-51afd.up.railway.app']
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# =============================================================================
# Aplicações Instaladas
# =============================================================================
INSTALLED_APPS = [
    # Apps do Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",  # intcomma: 133027 -> 133.027
    # Apps de terceiros
    "django_celery_beat",
    "channels",
    "django_htmx",
    # Apps do projeto
    "apps.accounts.apps.AccountsConfig",
    "apps.instagram.apps.InstagramConfig",
    "apps.publisher.apps.PublisherConfig",
    "apps.library.apps.LibraryConfig",
    "apps.analytics.apps.AnalyticsConfig",
    "apps.notifications.apps.NotificationsConfig",
    "apps.management.apps.ManagementConfig",
]

# =============================================================================
# Middleware
# =============================================================================
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"

# =============================================================================
# Templates
# =============================================================================
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.notifications.context_processors.notifications",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# =============================================================================
# Banco de Dados — PostgreSQL
# =============================================================================
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://autoinsta:autoinsta@localhost:5432/autoinsta",
    ),
}

# =============================================================================
# Cache — Redis
# =============================================================================
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    },
}

# =============================================================================
# Validação de Senhas
# =============================================================================
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# =============================================================================
# Internacionalização
# =============================================================================
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

# =============================================================================
# Arquivos Estáticos e Mídia
# =============================================================================
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
# Usamos CompressedStaticFilesStorage (sem manifesto) em vez de
# CompressedManifestStaticFilesStorage: o backend com manifesto lança
# "Missing staticfiles manifest entry" e quebra TODAS as páginas com
# DEBUG=False sempre que o collectstatic não tiver gerado o staticfiles.json.
# A versão sem manifesto continua comprimindo os arquivos, mas nunca derruba
# a página caso um estático esteja ausente.
# ATENÇÃO: ao definir STORAGES explicitamente, o Django NÃO mescla com os
# padrões. Sem a chave "default", qualquer upload de FileField estoura
# KeyError: 'default' (quebrava o Composer e a Biblioteca de Mídia).
STORAGES = {
    "default": {
        # Saneia o nome de TODO arquivo gravado (ver apps/core_utils.py):
        # a Meta não consegue baixar mídia com acento/espaço no nome.
        "BACKEND": "apps.core_utils.MidiaStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

# Servir estáticos via WhiteNoise mesmo com DEBUG=False no runserver local.
WHITENOISE_USE_FINDERS = True

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Envio de mídia processada BRAÇO -> PAINEL. O braço limpa/gera o vídeo no disco
# DELE; a Graph API (oficial) precisa baixar a mídia por URL pública do painel.
# Sem isso, contas SÓ-Graph falham com 404 em toda mídia processada.
#  - PAINEL_MEDIA_UPLOAD_URL: no braço, aponta para o endpoint do painel; vazio
#    no painel/máquina única (o arquivo já está onde o Caddy serve → no-op).
#  - MEDIA_UPLOAD_TOKEN: segredo compartilhado (MESMO nas duas máquinas).
PAINEL_MEDIA_UPLOAD_URL = env("PAINEL_MEDIA_UPLOAD_URL", default="")
MEDIA_UPLOAD_TOKEN = env("MEDIA_UPLOAD_TOKEN", default="")

# =============================================================================
# Modelo de Usuário Customizado
# =============================================================================
AUTH_USER_MODEL = "accounts.User"

# =============================================================================
# Autenticação — URLs
# =============================================================================
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

# =============================================================================
# Celery — Fila de Tarefas
# =============================================================================
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True

# Agendamento periódico (definido em código, sem depender do admin).
# Varre a fila a cada 60s e dispara as publicações cujo horário já chegou.
CELERY_BEAT_SCHEDULE = {
    "process-scheduled-posts": {
        # 30s (era 60s): posts "agora" saem mais rápido após subir a fila.
        # Não adiciona chamadas à Meta — só checa o banco com mais frequência.
        "task": "apps.publisher.tasks.process_scheduled_posts",
        "schedule": 30.0,
    },
    # Aquecimento gradual das contas (ações sociais em pequenos lotes).
    "run-account-warmups": {
        "task": "apps.instagram.tasks.run_warmups",
        "schedule": 1800.0,  # a cada 30 min
    },
    # Loops: enfileira a próxima mídia da pasta quando o intervalo vence.
    "process-loops": {
        "task": "apps.publisher.tasks.process_loops",
        "schedule": 60.0,
    },
    # Agenda semanal recorrente: cria os posts do plano quando o dia/hora chega.
    "process-agenda-semanal": {
        "task": "apps.publisher.tasks.process_agenda_semanal",
        "schedule": 900.0,  # a cada 15 min
    },
    # Atualiza cota/seguidores/views (Meta) de cada conta. A cada 3h: antes
    # era 30min, o que gerava ~192 chamadas por conta/dia num único app Meta —
    # peso desnecessário no volume que a Meta usa para marcar o app como abuso.
    "refresh-quotas": {
        "task": "apps.instagram.tasks.refresh_quotas",
        "schedule": 10800.0,  # a cada 3 horas
    },
    # Keep-alive das sessões: valida/renova o cookie das contas de sessionid a
    # cada 8h para não expirar (reduz muito as quedas). Vai para a fila publisher.
    "keepalive-sessions": {
        "task": "apps.instagram.tasks.keepalive_sessions",
        "schedule": 28800.0,  # a cada 8 horas
    },
    # Alertas: conta caiu, limite atingido, meta de views batida.
    "checar-alertas": {
        "task": "apps.notifications.tasks.checar_alertas",
        "schedule": 600.0,  # a cada 10 min
    },
    # Resumo do dia (só para quem ligou).
    "resumo-diario": {
        "task": "apps.notifications.tasks.resumo_diario",
        "schedule": crontab(hour=22, minute=0),
    },
    # Renovação automática do token da API oficial (long-lived ~60 dias): 1x/dia
    # renova quem está perto de vencer. É HTTP por token → fila leve (painel).
    "refresh-meta-tokens": {
        "task": "apps.instagram.tasks.refresh_meta_tokens",
        "schedule": crontab(hour=5, minute=30),
    },
}
CELERY_TASK_TIME_LIMIT = 300  # 5 minutos
CELERY_TASK_SOFT_TIME_LIMIT = 240  # 4 minutos
CELERY_WORKER_MAX_TASKS_PER_CHILD = 50

# ── Roteamento de filas: isolamento painel × braço ───────────────────────────
# O "braço" (servidor dedicado com IP limpo) roda SÓ as tarefas de
# engine/instagrapi — login, publicação, story-link, aquecimento e o downloader
# — consumindo a fila 'publisher'. O painel roda as tarefas leves (dispatchers
# do beat, métricas da Meta por token, notificações) na fila padrão 'celery'.
#
# RETROCOMPATÍVEL: enquanto tudo roda numa máquina só, o worker consome as DUAS
# filas (ver command dos compose: -Q ${CELERY_QUEUES:-celery,publisher}), então
# nada muda. Na virada, o braço sobe com CELERY_QUEUES=publisher e o painel com
# CELERY_QUEUES=celery — sem tocar no código, só variável de ambiente.
CELERY_TASK_DEFAULT_QUEUE = "celery"
CELERY_TASK_ROUTES = {
    "apps.publisher.tasks.publish_reel": {"queue": "publisher"},
    "apps.instagram.tasks.web_login_account": {"queue": "publisher"},
    "apps.instagram.tasks.connect_by_sessionid": {"queue": "publisher"},
    "apps.instagram.tasks.run_account_warmup": {"queue": "publisher"},
    "apps.instagram.tasks.bulk_edit_profiles": {"queue": "publisher"},
    "apps.instagram.tasks.keepalive_account": {"queue": "publisher"},
    "apps.library.tasks.run_profile_download": {"queue": "publisher"},
}

# =============================================================================
# Django Channels — WebSocket
# =============================================================================
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
        },
    },
}

# =============================================================================
# Chave Fernet — Criptografia de senhas do Instagram
# =============================================================================
FERNET_KEY = env("FERNET_KEY", default="")

# Sem FERNET_KEY, senhas/seeds/tokens não descriptografam e login/publicação
# falham em silêncio — e a chave PRECISA ser idêntica no painel e no braço.
# Em produção, falhar cedo (no boot) é melhor que descobrir isso a cada post.
if not DEBUG and not FERNET_KEY:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured(
        "FERNET_KEY vazia em produção. Defina-a no .env (a MESMA no painel e no "
        "braço) — sem ela, senhas e tokens do Instagram não descriptografam."
    )

# =============================================================================
# Configuração Padrão de Campo Primário
# =============================================================================
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# =============================================================================
# URL pública do site
# =============================================================================
# CRÍTICA para a publicação: a Meta baixa o vídeo/capa a partir desta URL.
# Se apontar para localhost, os servidores da Meta não conseguem acessar e a
# criação do contêiner de mídia falha sempre.
SITE_URL = env("SITE_URL", default="http://localhost:8000")

# Bot do Telegram usado para mandar os alertas ao celular. Opcional: se ficar
# vazio, cada usuário pode informar o token do próprio bot nas Configurações.
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")

# Confirmar na Meta se o post caiu na grade custa +1 chamada/post. Desligado
# por padrão para não engordar o volume de chamadas no app Meta (ver
# publish_reel). A grade quase sempre dá "sim"; ligue só para auditar.
VERIFICAR_GRADE = env.bool("VERIFICAR_GRADE", default=False)

# Variação automática de legenda por conta: cada conta posta uma versão única
# do texto (spintax {a|b|c} + caracteres invisíveis) para o Instagram não tirar
# a legenda por parecer conteúdo coordenado/duplicado em massa. Ligado.
VARIAR_LEGENDAS = env.bool("VARIAR_LEGENDAS", default=True)

# Espera (segundos) entre cada consulta de status ao publicar. A 1ª é longa de
# propósito: o Reel quase sempre já terminou de processar nela, então gastamos
# 1 consulta em vez de 8. É o principal corte de chamadas ao app Meta.
# Ajustável por .env sem novo deploy (lista separada por vírgula).
META_POLL_REEL = env.list("META_POLL_REEL", cast=int, default=[30, 25, 35, 50, 65])
META_POLL_IMAGE = env.list("META_POLL_IMAGE", cast=int, default=[10, 12, 20, 30])

# Web Push (PWA) — chaves VAPID. Geradas uma vez e postas no .env.
# Sem elas, o Web Push fica desligado (o alerta ainda aparece no sino).
VAPID_PUBLIC_KEY = env("VAPID_PUBLIC_KEY", default="")
VAPID_PRIVATE_KEY = env("VAPID_PRIVATE_KEY", default="")
VAPID_ADMIN_EMAIL = env("VAPID_ADMIN_EMAIL", default="admin@sandraoflow.com")

# =============================================================================
# Meta / Facebook API OAuth
# =============================================================================
META_APP_ID = env("META_APP_ID", default="")
META_APP_SECRET = env("META_APP_SECRET", default="")

# Proxy GLOBAL de login (instagrapi). O IP da VPS entra na blacklist do
# Instagram ("change your IP address...") e o login por usuário/senha e a
# validação de sessão passam a falhar para TODAS as contas. Definindo um proxy
# residencial/móvel aqui, todo login/sessão que não tenha proxy próprio da
# conta passa por esse IP e volta a funcionar. Formato:
#   http://user:pass@host:porta  (ou socks5://user:pass@host:porta)
IG_LOGIN_PROXY = env("IG_LOGIN_PROXY", default="")
# O redirect do OAuth PRECISA ser a URL pública do site — a Meta redireciona o
# navegador do usuário para cá depois da autorização. Se ficar em localhost, o
# callback nunca chega ao servidor e NENHUMA conta conecta por OAuth. Por isso
# derivamos do SITE_URL (que já é a URL pública) quando não vier explícito no
# .env. Lembrete: esta mesma URL precisa estar nos "Valid OAuth Redirect URIs"
# do app na Meta, senão a Meta bloqueia com "redirect_uri não permitido".
META_REDIRECT_URI = env(
    "META_REDIRECT_URI",
    default=f"{SITE_URL.rstrip('/')}/instagram/oauth/callback/",
)
# Segredo que assina o parâmetro `state` do OAuth (proteção anti-CSRF).
# Se vazio, o fluxo usa a SECRET_KEY do Django como chave de assinatura.
IG_STATE_SECRET = env("IG_STATE_SECRET", default="")

# =============================================================================
# OpenAI (geração de legendas com IA)
# =============================================================================
OPENAI_API_KEY = env("OPENAI_API_KEY", default="")
OPENAI_MODEL = env("OPENAI_MODEL", default="gpt-4o-mini")

# =============================================================================
# Limite de campos por requisição
# =============================================================================
# O padrão do Django (1000) estourava com "selecionar todos" em filas grandes
# (TooManyFieldsSent -> HTTP 400). A fila agora é paginada e o "selecionar
# todos" manda uma flag em vez de um campo por post, mas mantemos folga aqui.
DATA_UPLOAD_MAX_NUMBER_FIELDS = 20000

# =============================================================================
# Logging — em produção (DEBUG=False) o Django, sem esta config, NÃO imprime
# tracebacks de 500 no console. Aqui mandamos django.request e os apps para o
# stderr, para os erros aparecerem em `docker logs`.
# =============================================================================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "apps": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "engine": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # Fluxo de conexão de contas (prefixo [CONNECT] nas mensagens).
        "connect": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

