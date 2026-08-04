from django.urls import path
from . import views

app_name = 'publisher'

urlpatterns = [
    path('', views.queue_list, name='queue'),
    path('historico/', views.historico, name='historico'),
    path('campanha-ok/', views.campanha_ok, name='campanha_ok'),
    path('composer/', views.composer, name='composer'),
    path('add/', views.add_post, name='add'),
    path('remove/<int:post_id>/', views.remove_post, name='remove'),
    path('bulk/', views.bulk_posts, name='bulk_posts'),
    path('pause/', views.toggle_pause, name='toggle_pause'),
    path('fila/<int:queue_id>/pause/', views.toggle_queue_pause, name='toggle_queue_pause'),
    path('loops/', views.loops, name='loops'),
    path('loops/add/', views.add_loop, name='add_loop'),
    path('loops/toggle/<int:loop_id>/', views.toggle_loop, name='toggle_loop'),
    path('loops/delete/<int:loop_id>/', views.delete_loop, name='delete_loop'),
    path('stories/', views.stories, name='stories'),
    path('schedule/', views.schedule, name='schedule'),
    path('agenda/criar/', views.criar_agenda, name='criar_agenda'),
    path('agenda/editar/', views.editar_agenda, name='editar_agenda'),
    path('agenda/<int:agenda_id>/toggle/', views.toggle_agenda, name='toggle_agenda'),
    path('agenda/<int:agenda_id>/excluir/', views.excluir_agenda, name='excluir_agenda'),
    path('api/events/', views.api_events, name='api_events'),
    # Server-to-server: o braço envia mídia processada para o painel servir à Meta.
    path('internal/upload-media/', views.internal_upload_media, name='internal_upload_media'),
    path('caption-info/<int:set_id>/', views.caption_info, name='caption_info'),
    path('variar-preview/', views.variar_preview, name='variar_preview'),
]
