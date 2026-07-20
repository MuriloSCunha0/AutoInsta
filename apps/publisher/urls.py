from django.urls import path
from . import views

app_name = 'publisher'

urlpatterns = [
    path('', views.queue_list, name='queue'),
    path('add/', views.add_post, name='add'),
    path('remove/<int:post_id>/', views.remove_post, name='remove'),
    path('loops/', views.loops, name='loops'),
    path('stories/', views.stories, name='stories'),
    path('schedule/', views.schedule, name='schedule'),
    path('api/events/', views.api_events, name='api_events'),
]
