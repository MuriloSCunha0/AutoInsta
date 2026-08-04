FROM python:3.12-slim

# Variáveis de ambiente para Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Diretório de trabalho
WORKDIR /app

# Instalar dependências do sistema
# ffmpeg é usado na limpeza/diversificação de metadados dos vídeos antes de publicar.
# fonts-noto-color-emoji: SEM ela o gerador de CTA e o texto queimado no Story
# desenham um retângulo (.notdef) no lugar de cada emoji — a DejaVu não tem
# nenhum. É a fonte de emoji colorido que o Pillow consegue renderizar.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    ffmpeg \
    fonts-dejavu-core \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


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
