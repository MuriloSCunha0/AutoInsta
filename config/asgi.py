"""
Configuração ASGI para o projeto AutoInsta.

Inclui roteamento de WebSocket via Django Channels
para notificações em tempo real.
"""
import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Inicializar o ASGI do Django primeiro
django_asgi_app = get_asgi_application()

# Importar rotas de WebSocket após o Django estar configurado
from apps.notifications.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AuthMiddlewareStack(
            URLRouter(websocket_urlpatterns)
        ),
    }
)
