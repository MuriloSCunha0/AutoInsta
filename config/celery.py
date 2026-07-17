"""
Configuração do Celery para o projeto AutoInsta.

Descobre automaticamente tarefas em todos os apps registrados.
"""
import os

from celery import Celery

# Definir módulo de settings padrão do Django para o Celery
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("autoinsta")

# Carregar configurações do Django com prefixo CELERY_
app.config_from_object("django.conf:settings", namespace="CELERY")

# Descobrir tarefas automaticamente em todos os apps instalados
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Tarefa de debug para verificar se o Celery está funcionando."""
    print(f"Request: {self.request!r}")
