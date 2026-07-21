from django.urls import path
from . import views

app_name = 'instagram'

urlpatterns = [
    path('', views.account_list, name='list'),
    path('add/', views.add_account, name='add'),
    path('add-session/', views.add_account_session, name='add_session'),
    path('add-meta/', views.add_account_meta, name='add_meta'),
    path('meta/<int:account_id>/sync/', views.sync_meta_account, name='sync_meta_account'),
    path('meta/sync-all/', views.sync_all_meta, name='sync_all_meta'),
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
    path('proxies/add/', views.add_proxy, name='add_proxy'),
    path('proxies/toggle/<int:proxy_id>/', views.toggle_proxy, name='toggle_proxy'),
    path('proxies/delete/<int:proxy_id>/', views.delete_proxy, name='delete_proxy'),
    path('warmup/', views.warmup, name='warmup'),
    path('warmup/<int:account_id>/save/', views.warmup_save, name='warmup_save'),
    path('bulk-edit/', views.bulk_edit, name='bulk_edit'),
]
