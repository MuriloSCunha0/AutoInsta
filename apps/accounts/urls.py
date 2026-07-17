from django.urls import path
from django.contrib.auth.views import LogoutView
from django.views.decorators.csrf import csrf_exempt
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', csrf_exempt(views.CustomLoginView.as_view()), name='login'),
    path('force-login/', views.force_login, name='force_login'),
    path('logout/', LogoutView.as_view(next_page='accounts:login'), name='logout'),
    path('register/', views.register, name='register'),
]
