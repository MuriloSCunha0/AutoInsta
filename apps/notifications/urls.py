from django.urls import path
from . import views

app_name = 'notifications'

urlpatterns = [
    path('', views.list_notifications, name='list'),
    path('alertas/', views.alert_settings, name='alertas'),
    path('alertas/salvar/', views.alert_settings_save, name='alertas_salvar'),
    path('alertas/testar/', views.alert_test, name='alertas_testar'),
    path('push/key/', views.push_public_key, name='push_key'),
    path('push/subscribe/', views.push_subscribe, name='push_subscribe'),
    path('push/unsubscribe/', views.push_unsubscribe, name='push_unsubscribe'),
]
