from django.urls import path
from django.contrib.auth.views import LogoutView
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.CustomLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(next_page='accounts:login'), name='logout'),
    path('register/', views.register, name='register'),
    path('profile/', views.profile, name='profile'),
    path('profile/update/', views.profile_update, name='profile_update'),
    path('settings/', views.settings_view, name='settings'),
    path('settings/meta-credentials/', views.update_meta_credentials, name='update_meta_credentials'),
    path('settings/meta-apps/add/', views.add_meta_app, name='add_meta_app'),
    path('settings/meta-apps/<int:app_id>/update/', views.update_meta_app, name='update_meta_app'),
    path('settings/meta-apps/<int:app_id>/activate/', views.activate_meta_app, name='activate_meta_app'),
    path('settings/meta-apps/<int:app_id>/delete/', views.delete_meta_app, name='delete_meta_app'),
]
