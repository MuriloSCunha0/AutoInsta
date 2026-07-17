#!/bin/bash

# Aplica as migrações do banco de dados
echo "Verificando integridade do banco de dados..."
python fix_db.py

echo "Criando novas migrações (se necessário)..."
python manage.py makemigrations accounts instagram publisher library analytics notifications --noinput

echo "Aplicando migrações no banco de dados..."
python manage.py migrate --noinput

# Criar superuser se nao existir
export DJANGO_SUPERUSER_PASSWORD=admin
python manage.py createsuperuser --noinput --username admin --email admin@autoinsta.com || true

# Iniciar aplicacao
exec "$@"
