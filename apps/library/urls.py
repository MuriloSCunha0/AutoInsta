from django.urls import path
from . import views

app_name = 'library'

urlpatterns = [
    path('captions/', views.captions_list, name='captions'),
    path('captions/add/', views.add_caption, name='add_caption'),
    path('captions/<int:caption_id>/edit/', views.edit_caption, name='edit_caption'),
    path('captions/variation/<int:variation_id>/delete/', views.delete_variation, name='delete_variation'),
    path('captions/ai/generate/', views.generate_caption_ai, name='generate_caption_ai'),
    path('captions/delete/<int:caption_id>/', views.delete_caption, name='delete_caption'),
    path('audios/', views.audios_list, name='audios'),
    path('audios/add/', views.add_audio, name='add_audio'),
    path('audios/delete/<int:audio_id>/', views.delete_audio, name='delete_audio'),
    path('media/', views.media_list, name='media'),
    path('media/folder/add/', views.add_folder, name='add_folder'),
    path('media/folder/delete/<int:folder_id>/', views.delete_folder, name='delete_folder'),
    path('media/upload/', views.upload_media, name='upload_media'),
    path('media/delete/<int:asset_id>/', views.delete_media, name='delete_media'),
]
