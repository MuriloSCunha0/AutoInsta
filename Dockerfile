FROM python:3.12-slim

# Variáveis de ambiente para Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Diretório de trabalho
WORKDIR /app

# Instalar dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instalar o Chromium do Playwright + libs de sistema (para o login web real
# no instagram.com). --with-deps já traz as dependências nativas no Debian slim.
RUN python -m playwright install --with-deps chromium

# Copiar código do projeto
COPY . .

# Coletar arquivos estáticos (também é re-executado no entrypoint em runtime)
RUN python manage.py collectstatic --noinput

# Expor porta
EXPOSE 8000

# Scripts de inicialização
COPY entrypoint.sh /app/
COPY start.sh /app/
RUN chmod +x /app/entrypoint.sh /app/start.sh

# entrypoint faz migrações/collectstatic; start.sh sobe worker + beat + gunicorn
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["/app/start.sh"]
