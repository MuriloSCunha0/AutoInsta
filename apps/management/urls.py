from django.urls import path
from . import views

app_name = 'management'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('users/', views.users_list, name='users'),
    path('users/<int:user_id>/toggle/', views.user_toggle_active, name='user_toggle'),
    path('instagram/', views.instagram_list, name='instagram'),
    path('posts/', views.posts_list, name='posts'),
]
