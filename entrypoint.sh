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
# Criar superuser apenas se não existir
python manage.py shell << END
from django.contrib.auth.models import User
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@autoinsta.com', os.environ.get('DJANGO_SUPERUSER_PASSWORD', 'admin'))
    print("Superuser 'admin' criado com sucesso")
else:
    print("Superuser 'admin' já existe")
END

# Iniciar aplicacao
exec "$@"
