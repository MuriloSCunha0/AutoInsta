from django.urls import path
from . import views

app_name = 'notifications'

urlpatterns = [
    path('', views.list_notifications, name='list'),
    path('alertas/', views.alert_settings, name='alertas'),
    path('alertas/salvar/', views.alert_settings_save, name='alertas_salvar'),
    path('alertas/testar/', views.alert_test, name='alertas_testar'),
]
