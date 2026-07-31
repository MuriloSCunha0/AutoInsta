# -*- coding: utf-8 -*-
"""
Deploy da OVH (2 máquinas), SEM derrubar publicações em andamento.

Arquitetura (ver DEPLOY_OVH.md / memória ovh-migracao-2maquinas):
  - PAINEL (A) 148.113.167.188 : web + Postgres + Redis + Beat + Caddy
  - BRAÇO  (B) 15.235.51.234   : worker da engine (fila publisher), IP limpo

Fluxo:
  1. git reset --hard origin/main nas duas.
  2. Build das imagens.
  3. Painel: migra num container efêmero (schema antes do código novo) e recria
     beat/web/worker; Caddy recarrega a quente (sem downtime).
  4. Braço: recria o worker, que DRENA o antigo (stop_grace_period=330s) — nenhuma
     publicação em andamento é morta.

Pré-requisito: `git push origin main` ANTES de rodar (o servidor puxa do origin).
Chave SSH: ~/.ssh/sandraoflow_ovh (ou defina OVH_SSH_KEY). Usuário: ubuntu (sudo).
"""
import os
import sys

import paramiko

KEY = os.path.expanduser(os.environ.get("OVH_SSH_KEY", "~/.ssh/sandraoflow_ovh"))
USER = "ubuntu"
PANEL = "148.113.167.188"
BRACO = "15.235.51.234"

PANEL_CMD = r"""
set -e
cd /opt/sandraoflow
echo '==> [painel] 1/4 sincronizando codigo'
git fetch origin -q && git reset --hard origin/main && git log --oneline -1
CF="sudo docker compose -f docker-compose.prod.yml -f docker-compose.painel.yml"
echo '==> [painel] 2/4 build'
$CF build web
echo '==> [painel] 3/4 migracoes (container efemero)'
$CF run --rm -T --no-deps web sh -c "python fix_db.py && python manage.py migrate --noinput"
echo '==> [painel] 4/4 recria beat/web/worker + reload Caddy (a quente)'
$CF up -d --no-deps celery_beat web celery_worker
$CF exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile || $CF up -d --no-deps caddy
echo '==> [painel] OK'
"""

BRACO_CMD = r"""
set -e
cd /opt/sandraoflow
echo '==> [braco] 1/2 sincronizando codigo'
git fetch origin -q && git reset --hard origin/main && git log --oneline -1
echo '==> [braco] 2/2 build + recria worker (drena ate 330s)'
sudo docker compose -f docker-compose.braco.yml build celery_worker
sudo docker compose -f docker-compose.braco.yml up -d celery_worker
echo '==> [braco] OK'
"""


def run(host, cmd, label):
    print(f"\n########## {label}  ({host}) ##########", flush=True)
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(host, username=USER, key_filename=KEY, timeout=25)
    _, out, _ = cli.exec_command(cmd, timeout=900, get_pty=True)
    for line in iter(out.readline, ""):
        sys.stdout.write(line)
        sys.stdout.flush()
    rc = out.channel.recv_exit_status()
    cli.close()
    if rc != 0:
        print(f"!! {label} terminou com codigo {rc}", flush=True)
    return rc


if __name__ == "__main__":
    if not os.path.exists(KEY):
        sys.exit(f"Chave SSH nao encontrada: {KEY} (defina OVH_SSH_KEY)")
    # Painel primeiro (schema/migracoes), depois o braço.
    rc = run(PANEL, PANEL_CMD, "PAINEL")
    rc |= run(BRACO, BRACO_CMD, "BRACO")
    print("\n==> deploy OVH concluido" if rc == 0 else "\n==> deploy OVH terminou COM ERROS", flush=True)
    sys.exit(rc)
