#!/bin/bash

# Aplica as migrações do banco de dados
echo "Verificando integridade do banco de dados..."
python fix_db.py

echo "Criando novas migrações (se necessário)..."
python manage.py makemigrations accounts instagram publisher library analytics notifications --noinput

echo "Aplicando migrações no banco de dados..."
python manage.py migrate --noinput

echo "Coletando arquivos estáticos..."
python manage.py collectstatic --noinput --clear

# Criar superuser se nao existir
# Criar superuser se nao existir
python manage.py shell << 'END'

import os
from django.contrib.auth import get_user_model

User = get_user_model()
username = 'admin'
email = 'admin@autoinsta.com'
password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', 'admin')

if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username, email, password)
    print("Superuser 'admin' criado com sucesso")
else:
    print("Superuser 'admin' já existe")
    
END

# Iniciar aplicacao
exec "$@"
