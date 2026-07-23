from django.urls import path
from . import views

app_name = 'management'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('users/', views.users_list, name='users'),
    path('users/purge-unapproved/', views.users_purge_unapproved, name='users_purge'),
    path('users/<int:user_id>/toggle/', views.user_toggle_active, name='user_toggle'),
    path('users/<int:user_id>/ip-lock/', views.user_toggle_ip_lock, name='user_ip_lock'),
    path('users/<int:user_id>/ip-reset/', views.user_reset_ip, name='user_ip_reset'),
    path('users/<int:user_id>/delete/', views.user_delete, name='user_delete'),
    path('instagram/', views.instagram_list, name='instagram'),
    path('posts/', views.posts_list, name='posts'),
    path('moderacao/', views.moderation, name='moderation'),
    path('moderacao/conta/<int:account_id>/', views.moderation_account, name='moderation_account'),
    path('moderacao/conta/<int:account_id>/ban/', views.account_ban, name='account_ban'),
]
