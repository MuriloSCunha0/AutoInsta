import paramiko
import time
import sys

host = '179.197.230.238'
user = 'root'
password = '#CardingCarding123'

commands = [
    # Atualiza pacotes e instala dependencias basicas
    "apt-get update",
    "apt-get install -y docker.io docker-compose git",
    
    # Prepara o diretorio do projeto
    "mkdir -p /opt/sandraoflow",
    
    # Clona ou atualiza o repositorio
    "if [ -d '/opt/sandraoflow/.git' ]; then cd /opt/sandraoflow && git reset --hard && git pull; else git clone https://github.com/MuriloSCunha0/AutoInsta.git /opt/sandraoflow; fi",
    
    # Cria o .env de producao (substituindo o dummy)
    """cat << 'EOF' > /opt/sandraoflow/.env
DEBUG=False
SECRET_KEY=sandraoflow-super-secret-key-prod-2024
DJANGO_ALLOWED_HOSTS=sandraoflow.com,179.197.230.238,localhost
CSRF_TRUSTED_ORIGINS=https://sandraoflow.com,http://179.197.230.238
POSTGRES_DB=sandraoflow_db
POSTGRES_USER=sandraoflow_user
POSTGRES_PASSWORD=sandraoflow_pass_secure
REDIS_URL=redis://redis:6379/0
DATABASE_URL=postgres://sandraoflow_user:sandraoflow_pass_secure@db:5432/sandraoflow_db
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
EOF""",

    # Faz o build e sobe os containers com o arquivo de producao
    "cd /opt/sandraoflow && docker-compose -f docker-compose.prod.yml down",
    "cd /opt/sandraoflow && docker-compose -f docker-compose.prod.yml build",
    "cd /opt/sandraoflow && docker-compose -f docker-compose.prod.yml up -d",
    
    # Roda as migracoes e coleta estaticos
    "cd /opt/sandraoflow && docker-compose -f docker-compose.prod.yml exec -T web python manage.py migrate",
    "cd /opt/sandraoflow && docker-compose -f docker-compose.prod.yml exec -T web python manage.py collectstatic --noinput"
]

print(f"Iniciando deploy na VPS {host}...")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

try:
    ssh.connect(host, username=user, password=password, timeout=10)
    for cmd in commands:
        print(f"\n--- Executando: {cmd[:50]}... ---")
        stdin, stdout, stderr = ssh.exec_command(cmd)
        
        # Leitura da saida
        exit_status = stdout.channel.recv_exit_status()
        out = stdout.read().decode('utf-8', errors='ignore').strip()
        err = stderr.read().decode('utf-8', errors='ignore').strip()
        
        if out:
            try:
                print(out.encode(sys.stdout.encoding, errors='replace').decode(sys.stdout.encoding))
            except Exception:
                print("Output received (not printable)")
        if err and exit_status != 0:
            try:
                print(f"ERRO: {err.encode(sys.stderr.encoding, errors='replace').decode(sys.stderr.encoding)}", file=sys.stderr)
            except Exception:
                print("Error received (not printable)")
            
    print("\nDeploy concluido com sucesso!")
except Exception as e:
    print(f"Falha ao conectar ou executar: {e}")
finally:
    ssh.close()
