from django.urls import path

from . import views

app_name = 'pressel'

urlpatterns = [
    path('', views.lista, name='lista'),
    path('nova/', views.nova, name='nova'),
    path('<int:pressel_id>/', views.editar, name='editar'),
    path('<int:pressel_id>/previa/', views.previa, name='previa'),
    path('<int:pressel_id>/baixar/', views.baixar, name='baixar'),
    path('<int:pressel_id>/duplicar/', views.duplicar, name='duplicar'),
    path('<int:pressel_id>/excluir/', views.excluir, name='excluir'),
]
