from django.urls import path
from . import views

app_name = 'library'

urlpatterns = [
    path('captions/', views.captions_list, name='captions'),
    path('captions/add/', views.add_caption, name='add_caption'),
    path('captions/delete/<int:caption_id>/', views.delete_caption, name='delete_caption'),
    path('audios/', views.audios_list, name='audios'),
    path('audios/add/', views.add_audio, name='add_audio'),
    path('audios/delete/<int:audio_id>/', views.delete_audio, name='delete_audio'),
]
