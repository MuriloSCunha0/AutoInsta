import paramiko

host = '179.197.230.238'
user = 'root'
password = '#CardingCarding123'

python_script = """
import os
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from apps.accounts.models import User
from apps.instagram.models import InstagramAccount
from apps.instagram.tasks import web_login_account

try:
    owner = User.objects.get(username='sandrao')
except User.DoesNotExist:
    owner = User.objects.first()

acc, created = InstagramAccount.objects.get_or_create(
    owner=owner,
    ig_username='Beatriz.soleto_'
)
acc.set_ig_password('#Carding1')
acc.status = 'connecting'
acc.save()

print("Disparando task...")
web_login_account.delay(acc.id)

print("Aguardando celery processar o login...")
for _ in range(30):
    time.sleep(2)
    acc.refresh_from_db()
    print(f"Status atual: {acc.status} | Ultimo erro: {acc.last_error}")
    if acc.status != 'connecting':
        break
"""

create_cmd = f"""
cat << 'EOF' | docker-compose -f /opt/sandraoflow/docker-compose.prod.yml exec -T web python
{python_script}
EOF
"""

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    ssh.connect(host, username=user, password=password, timeout=10)
    stdin, stdout, stderr = ssh.exec_command(create_cmd)
    
    out = stdout.read().decode('utf-8', errors='ignore').strip()
    err = stderr.read().decode('utf-8', errors='ignore').strip()
    
    print("SAIDA:", out)
    if err:
        print("ERRO:", err)
except Exception as e:
    print(f"Erro: {e}")
finally:
    ssh.close()
