from django.urls import path
from . import views

app_name = 'instagram'

urlpatterns = [
    path('', views.account_list, name='list'),
    path('add/', views.add_account, name='add'),
    path('status/<int:account_id>/', views.account_status_partial, name='status'),
    path('challenge/<int:account_id>/', views.submit_challenge, name='submit_challenge'),
    path('remove/<int:account_id>/', views.remove_account, name='remove'),
]
