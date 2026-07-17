"""
Configurações do Django para o projeto AutoInsta.

Plataforma SaaS multi-tenant de automação para Instagram.
"""
import os
from pathlib import Path

import environ

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
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

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
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300  # 5 minutos
CELERY_TASK_SOFT_TIME_LIMIT = 240  # 4 minutos
CELERY_WORKER_MAX_TASKS_PER_CHILD = 50

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

# =============================================================================
# Configuração Padrão de Campo Primário
# =============================================================================
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
