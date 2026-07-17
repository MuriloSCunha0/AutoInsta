#!/bin/bash

# Aplicar migrations
python manage.py makemigrations
python manage.py migrate

# Criar superuser se nao existir
export DJANGO_SUPERUSER_PASSWORD=admin
python manage.py createsuperuser --noinput --username admin --email admin@autoinsta.com || true

# Iniciar aplicacao
exec "$@"
