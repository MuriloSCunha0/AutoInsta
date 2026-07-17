"""
Configuração de URLs raiz do projeto AutoInsta.

Inclui as URLs de todos os apps e redireciona a raiz para o dashboard.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path


def root_redirect(request):
    """Redireciona a raiz do site para o dashboard."""
    return redirect("analytics:dashboard")

urlpatterns = [
    # Admin do Django
    path("admin/", admin.site.urls),
    # Redirecionamento da raiz
    path("", root_redirect, name="root"),
    # Dashboard (aponta para publisher como página principal)
    path("dashboard/", root_redirect, name="dashboard"),
    # URLs dos apps
    path("accounts/", include("apps.accounts.urls", namespace="accounts")),
    path("instagram/", include("apps.instagram.urls", namespace="instagram")),
    path("publisher/", include("apps.publisher.urls", namespace="publisher")),
    path("library/", include("apps.library.urls", namespace="library")),
    path("analytics/", include("apps.analytics.urls", namespace="analytics")),
    path("notifications/", include("apps.notifications.urls", namespace="notifications")),
]

# Servir arquivos de mídia em desenvolvimento
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
