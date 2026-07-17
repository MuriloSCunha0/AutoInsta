#!/bin/bash
#
# Inicia todos os processos da aplicação em um único container (caminho simples):
#   - Celery worker  -> executa as tarefas (login no Instagram, upload de reels)
#   - Celery beat     -> dispara o process_scheduled_posts periodicamente
#   - Gunicorn        -> serve o Django (fica em foreground = mantém o container vivo)
#
set -e

echo "Iniciando Celery worker..."
celery -A config worker --loglevel=info --concurrency=2 &

echo "Iniciando Celery beat (agendador)..."
celery -A config beat --loglevel=info &

echo "Iniciando Gunicorn..."
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers 3 \
    --timeout 120
