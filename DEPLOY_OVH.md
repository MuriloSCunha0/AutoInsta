# Migração para OVH — Painel (A) + Braço isolado (B)

Objetivo: sair 100% da Hostinger (mantendo só o domínio registrado lá) e rodar
a operação em duas máquinas OVH, com o Instagram isolado num IP dedicado limpo.

```
   Usuários ──HTTPS──►  [ A: PAINEL ]  ── WireGuard ──►  [ B: BRAÇO ]  ──► Instagram
                        VPS-3 / BHS                      Rise-1 / BHS       (IP limpo)
                        web+db+redis+beat+caddy          worker engine/instagrapi
```

- **A (Painel):** web (gunicorn) + Postgres + Redis + Celery Beat + Caddy. Não fala com o IG.
- **B (Braço):** só o Celery worker da fila `publisher` — login, publicação, story-link, aquecimento, downloader. Autentica e sobe pelo **IP limpo**.
- **Link A↔B:** WireGuard. Redis/Postgres nunca expostos na internet.

O código já está preparado (retrocompatível): roteamento de filas, worker `-Q`
por env e `garantir_midia_local()` (o braço baixa a mídia-fonte do painel sob
demanda). Enquanto tudo roda numa máquina só, nada muda.

---

## 0. Contratar (OVH, região BHS / Canadá, Ubuntu 24.04)
- **A:** VPS-3 (6 vCPU / 12 GB / 100 GB NVMe).
- **B:** Rise-1 (Xeon-E 6c/12t / 32 GB ECC / 2×512 GB NVMe) — config **NVMe**.

## 1. Base das duas máquinas
```bash
apt update && apt -y install docker.io docker-compose-plugin git wireguard
```

## 2. WireGuard (túnel A↔B)
Rede privada sugerida: A=10.8.0.1, B=10.8.0.2.
- Gerar chaves nos dois: `wg genkey | tee privatekey | wg pubkey > publickey`
- `/etc/wireguard/wg0.conf` em **A**:
  ```
  [Interface]
  Address = 10.8.0.1/24
  ListenPort = 51820
  PrivateKey = <priv A>
  [Peer]
  PublicKey = <pub B>
  AllowedIPs = 10.8.0.2/32
  ```
- **B** espelhado (Address 10.8.0.2/24, Peer = pub A, Endpoint = <IP público A>:51820, AllowedIPs = 10.8.0.1/32, PersistentKeepalive = 25).
- `systemctl enable --now wg-quick@wg0` nos dois. Testar: `ping 10.8.0.1` de B.

## 3. Painel (A)
```bash
git clone <repo> /opt/sandraoflow && cd /opt/sandraoflow
cp .env.example .env   # e preencher (ver abaixo)
```
`.env` do painel (essencial):
```
DEBUG=False
SECRET_KEY=...
FERNET_KEY=...                         # a MESMA de hoje (não gerar nova!)
DATABASE_URL=postgres://autoinsta:...@db:5432/autoinsta
REDIS_URL=redis://redis:6379/0
SITE_URL=https://sandraoflow.com
CELERY_QUEUES=celery                   # painel: só tarefas leves
```
- Postgres/Redis precisam aceitar conexão pela rede WireGuard (bind na 10.8.0.1
  ou publicar a porta só na interface wg0). **Nunca** expor na internet.
- Subir: `docker compose -f docker-compose.prod.yml up -d --build`

## 4. Migrar o banco (Hostinger → A)
```bash
# na Hostinger:
docker exec -t sandraoflow-db-1 pg_dump -U postgres postgres | gzip > dump.sql.gz
scp dump.sql.gz root@<A>:/opt/sandraoflow/
# em A:
zcat dump.sql.gz | docker exec -i sandraoflow-db-1 psql -U postgres postgres
```
Copiar também o volume de **mídia** (reels/imagens já enviados) da Hostinger p/ A.

## 5. Braço (B)
```bash
git clone <repo> /opt/sandraoflow && cd /opt/sandraoflow
cp .env.example .env
```
`.env` do braço:
```
DEBUG=False
SECRET_KEY=...                         # mesma do painel
FERNET_KEY=...                         # MESMA do painel (decifra senha/seed)
DATABASE_URL=postgres://autoinsta:...@10.8.0.1:5432/autoinsta   # painel via WG
REDIS_URL=redis://10.8.0.1:6379/0                              # painel via WG
CELERY_QUEUES=publisher                # braço: só a engine
CELERY_CONCURRENCY=24
SITE_URL=https://sandraoflow.com       # de onde baixar a mídia-fonte (painel)
```
- Subir: `docker compose -f docker-compose.braco.yml up -d --build`
- Conferir: `docker compose -f docker-compose.braco.yml logs -f celery_worker`
  (deve conectar no Redis 10.8.0.1 e ficar ouvindo a fila `publisher`).

### Observação sobre o caminho GRAPH API no braço
O caminho **engine (instagrapi)** — o foco — sobe os bytes direto: nada a fazer.
O caminho **Graph API** faz a Meta baixar a mídia por URL. Com a limpeza ligada
(light/ultra, padrão), o arquivo processado fica no braço; para a Meta alcançá-lo,
descomente o serviço `caddy_media` no `docker-compose.braco.yml`, crie o
`Caddyfile.braco` servindo `/srv/media` no domínio do braço e ajuste `SITE_URL`
do braço para essa URL. Contas **só-token** que publicam sem limpeza continuam
podendo usar o painel; decidir no provisioning.

## 6. Validar sem downtime
- Testar num subdomínio (ex.: `teste.sandraoflow.com` → A): login, publicar 1
  post por conta com sessão (sai pelo IP do braço), story-link e um download.
- Confirmar no braço: `docker ... logs` mostra o `publish_reel` rodando lá.

## 7. Virada de DNS (o domínio fica na Hostinger)
- No painel DNS da **Hostinger**, apontar o registro **A** de `sandraoflow.com`
  (e `www`) para o **IP público da Máquina A**.
- Caddy da A emite o TLS novo automaticamente.
- Baixar o TTL antes p/ propagar rápido; validar HTTPS.

## 8. Desligar a Hostinger
- Com A+B validados e DNS propagado, parar os containers na Hostinger e cancelar
  a VPS. **Manter o domínio** (já pago por 1 ano) apontando para a OVH.

---

## Rollback
Enquanto a Hostinger não for cancelada, reverter é só reapontar o DNS de volta
para o IP dela. Por isso o passo 8 é o último.

## Divisão de filas (referência)
- **publisher** (braço): `publish_reel`, `web_login_account`, `connect_by_sessionid`,
  `run_account_warmup`, `bulk_edit_profiles`, `run_profile_download`.
- **celery** (painel): dispatchers do beat (`process_scheduled_posts`, `process_loops`,
  `run_warmups`), `refresh_quotas` (Meta/token), alertas e resumo.
