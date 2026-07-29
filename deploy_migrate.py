# -*- coding: utf-8 -*-
"""
Deploy SEM derrubar publicacoes em andamento.

Estrategia:
  1. Sincroniza o codigo (limpa migrations untracked geradas no servidor).
  2. Faz o BUILD de todas as imagens (nao toca nos containers rodando).
  3. Aplica migracoes num container EFEMERO da imagem nova (schema pronto
     antes do codigo novo subir; migracoes devem ser retrocompativeis).
  4. Recria beat -> web -> worker, nessa ordem:
       - web: gunicorn faz shutdown gracioso; Caddy re-tenta o upstream.
       - Caddy: reload A QUENTE (sem recriar o container, zero downtime).
       - worker: DRENA o antigo (stop_grace_period=330s) -> termina o que
         esta publicando antes de sair. Nenhuma tarefa em andamento e morta.

IMPORTANTE: mantenha as migracoes retrocompativeis (aditivas). Enquanto o
worker antigo drena, ele ainda roda o CODIGO VELHO contra o schema NOVO.
"""
import sys
import paramiko

host = '179.197.230.238'
user = 'root'
password = '#CardingCarding123'

COMPOSE = 'docker compose -f docker-compose.prod.yml'

deploy_cmd = f"""
set -e
cd /opt/sandraoflow

echo '==> 1/6 sincronizando codigo'
git fetch origin
# Remove migrations untracked geradas no servidor (nao ha mais makemigrations
# em prod, mas limpa restos que travariam o pull). git clean nao mexe em
# arquivos ignorados (.env, __pycache__).
git clean -fd apps || true
git checkout -- . || true
git reset --hard origin/main
git log --oneline -1

echo '==> 2/6 build das imagens (sem afetar o que roda)'
{COMPOSE} build web celery_worker celery_beat

echo '==> 3/6 migracoes (container efemero da imagem nova)'
{COMPOSE} run --rm -T --no-deps web sh -c "python fix_db.py && python manage.py migrate --noinput"

echo '==> 4/6 recriando beat'
{COMPOSE} up -d --no-deps celery_beat

echo '==> 5/6 recriando web (gunicorn gracioso) + reload do Caddy'
{COMPOSE} up -d --no-deps web
{COMPOSE} exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile || \
  {COMPOSE} up -d --no-deps caddy

echo '==> 6/6 drenando e recriando o worker (ate 330s p/ terminar publicacoes)'
{COMPOSE} up -d --no-deps celery_worker

echo '==> OK deploy concluido'
"""

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    ssh.connect(host, username=user, password=password, timeout=15)
    # get_pty=True junta stdout/stderr e evita travar em buffers grandes
    stdin, stdout, stderr = ssh.exec_command(deploy_cmd, timeout=600, get_pty=True)
    for line in iter(stdout.readline, ''):
        sys.stdout.buffer.write(line.encode('utf-8', 'ignore'))
        sys.stdout.flush()
    err = stderr.read().decode('utf-8', 'ignore').strip()
    if err:
        sys.stdout.buffer.write(b"\n--- STDERR ---\n" + err.encode('utf-8', 'ignore'))
except Exception as e:
    print(f"Erro: {e}")
finally:
    ssh.close()
