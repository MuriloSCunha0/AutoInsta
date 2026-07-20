from django.urls import path
from . import views

app_name = 'instagram'

urlpatterns = [
    path('', views.account_list, name='list'),
    path('add/', views.add_account, name='add'),
    path('add-session/', views.add_account_session, name='add_session'),
    path('add-meta/', views.add_account_meta, name='add_meta'),
    path('oauth/url/', views.oauth_url, name='oauth_url'),
    path('oauth/callback/', views.oauth_callback, name='oauth_callback'),
    path('connect-extension/', views.connect_extension, name='connect_extension'),
    path('extension-token/regenerate/', views.regenerate_extension_token, name='regenerate_token'),
    path('status/<int:account_id>/', views.account_status_partial, name='status'),
    path('challenge/<int:account_id>/', views.submit_challenge, name='submit_challenge'),
    path('challenge/<int:account_id>/resend/', views.resend_challenge, name='resend_challenge'),
    path('remove/<int:account_id>/', views.remove_account, name='remove'),
    path('profile/', views.profile, name='profile'),
    path('proxies/', views.proxies, name='proxies'),
]
