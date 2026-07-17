from django.urls import path
from . import views

app_name = 'library'

urlpatterns = [
    path('captions/', views.captions_list, name='captions'),
    path('audios/', views.audios_list, name='audios'),
]
