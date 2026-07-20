from django.urls import path
from . import views

app_name = 'analytics'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('performance/', views.performance, name='performance'),
    path('top-posts/', views.top_posts, name='top_posts'),
    path('top-posts/sync/', views.sync_top_posts, name='sync_top_posts'),
    path('health/', views.health, name='health'),
    path('logs/', views.logs_view, name='logs'),
]
