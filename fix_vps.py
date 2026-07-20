import paramiko
import sys

host = '179.197.230.238'
user = 'root'
password = '#CardingCarding123'

create_cmd = """
cd /opt/sandraoflow
git pull
docker compose -f docker-compose.prod.yml build web
docker compose -f docker-compose.prod.yml up -d --force-recreate web
"""

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    ssh.connect(host, username=user, password=password, timeout=10)
    stdin, stdout, stderr = ssh.exec_command(create_cmd)
    
    out = stdout.read().decode('utf-8', errors='ignore').strip()
    print("SAIDA:", out)
    err = stderr.read().decode('utf-8', errors='ignore').strip()
    if err:
        print("ERRO:", err)
except Exception as e:
    print(f"Erro: {e}")
finally:
    ssh.close()
