#!/bin/bash
#
# Entrypoint CIENTE DO PAPEL do container.
#
# Só o processo WEB faz o bootstrap do banco/estáticos. Worker e beat sobem
# direto (exec), sem migrar. Isso permite deploy sem derrubar publicações:
#   - o worker novo sobe na hora (não fica preso migrando/coletando estáticos);
#   - não há 3 `migrate` correndo em paralelo (web + worker + beat);
#   - a drenagem graciosa do worker antigo não disputa o banco.
#
# As migrações são aplicadas UMA vez, no passo dedicado do deploy e no boot do
# web (idempotente). `makemigrations` NÃO roda em produção por padrão — as
# migrações vêm versionadas do git; gerá-las no servidor criava arquivos
# untracked que travavam o `git pull`. Em dev, ligue com AUTO_MAKEMIGRATIONS=1.

set -e

case "$1" in
  *gunicorn*|*runserver*)
    echo "[entrypoint] web: bootstrap do banco/estáticos"

    echo "  - verificando integridade do banco..."
    python fix_db.py || true

    if [ "${AUTO_MAKEMIGRATIONS:-0}" = "1" ]; then
      echo "  - gerando migrações (AUTO_MAKEMIGRATIONS=1)..."
      python manage.py makemigrations accounts instagram publisher library analytics notifications --noinput
    fi

    echo "  - aplicando migrações..."
    python manage.py migrate --noinput

    # Sem --clear: apagar os estáticos no boot deixa uma janela de CSS 404
    # enquanto o web sobe (o Caddy serve o volume compartilhado).
    echo "  - coletando estáticos..."
    python manage.py collectstatic --noinput

    echo "  - garantindo superuser..."
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
    ;;
  *)
    echo "[entrypoint] $1: sobe direto, sem bootstrap do banco"
    ;;
esac

# Inicia o processo pedido (gunicorn / celery worker / celery beat / ...).
exec "$@"
