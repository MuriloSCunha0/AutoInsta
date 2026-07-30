"""Tasks da biblioteca — por enquanto, o Downloader de perfil.

Baixa o conteúdo de um perfil do Instagram (feed, reels, stories atuais e
destaques) usando a sessão instagrapi de UMA das contas do usuário e entrega
tudo num ZIP, junto de um `legendas.txt` (arquivo → legenda) para repostar com
o texto original. A API oficial da Meta não lê perfil de terceiros, então isto
roda pela engine cinza (mesma que faz login por sessão/senha).
"""
import logging
import os
import re
import shutil
import time
import zipfile

import random

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.utils import timezone

from engine.client import InstagramEngine
from engine.session_manager import SessionManager

logger = logging.getLogger('downloader')


def _cliente_logado(account):
    """Devolve um client instagrapi autenticado para LER o perfil-alvo.

    Prioriza a sessão salva (não gasta login novo); só cai para login por senha
    se a sessão não valer e a conta tiver senha real. Contas conectadas só por
    token da Meta não servem — a leitura de perfil é pela engine.
    """
    eng = InstagramEngine(account)
    cl = eng.client
    # Mesma blindagem de versão usada no login normal.
    cl.device_settings["app_version"] = "361.0.0.39.109"
    cl.device_settings["version_code"] = "574767436"
    eng._aplicar_proxy()

    tinha_sessao = SessionManager.load_session(account, cl)
    if tinha_sessao:
        try:
            cl.get_timeline_feed()  # valida a sessão com uma chamada barata
            return cl
        except Exception:
            logger.info("[DL] sessão de @%s expirada — tentando login por senha", account.ig_username)

    senha = ''
    try:
        senha = account.get_ig_password()
    except Exception:
        senha = ''
    if senha and senha != '__session_login__':
        SessionManager.ensure_device(account, cl)
        cl.login(account.ig_username, senha)
        SessionManager.save_session(account, cl)
        return cl

    raise RuntimeError(
        f"A conta @{account.ig_username} não tem sessão válida para ler perfis. "
        "Reconecte-a (sessão/senha) e tente de novo."
    )


def _slug(texto):
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', (texto or '').strip()) or 'perfil'


def _pausa():
    """Pequena pausa entre downloads — ler perfil inteiro de uma vez é o que mais
    sinaliza a conta usada. Não elimina o risco, só reduz o ritmo."""
    time.sleep(random.uniform(0.6, 1.6))


def _baixar_medias(cl, uid, limite, quer_feed, quer_reels, pasta, legendas):
    """Baixa posts do feed/reels do perfil. Retorna quantos itens (arquivos) saiu.

    - media_type: 1=foto, 2=vídeo, 8=carrossel.
    - product_type == 'clips' → é Reel; senão é feed comum.
    """
    baixados = 0
    end_cursor = None
    vistos = 0
    while True:
        try:
            chunk, end_cursor = cl.user_medias_paginated(uid, 24, end_cursor=end_cursor or "")
        except Exception as e:
            logger.warning("[DL] paginação de mídias parou: %s", str(e)[:120])
            break
        if not chunk:
            break
        for m in chunk:
            vistos += 1
            eh_reel = getattr(m, 'product_type', '') == 'clips'
            if eh_reel and not quer_reels:
                continue
            if (not eh_reel) and not quer_feed:
                continue
            try:
                pk = int(m.pk)
                if m.media_type == 1:
                    caminhos = [cl.photo_download(pk, folder=pasta)]
                elif m.media_type == 8:
                    caminhos = cl.album_download(pk, folder=pasta)
                else:
                    caminhos = [cl.video_download(pk, folder=pasta)]
            except Exception as e:
                logger.warning("[DL] falhou baixar mídia %s: %s", getattr(m, 'pk', '?'), str(e)[:120])
                continue

            legenda = (getattr(m, 'caption_text', '') or '').strip()
            for c in caminhos:
                if not c:
                    continue
                baixados += 1
                if legenda:
                    legendas.append((os.path.basename(str(c)), legenda))
            _pausa()
            if limite and vistos >= limite:
                return baixados
        if limite and vistos >= limite:
            break
        if not end_cursor:
            break
    return baixados


def _baixar_stories(cl, uid, pasta):
    baixados = 0
    try:
        stories = cl.user_stories(uid)
    except Exception as e:
        logger.warning("[DL] stories indisponíveis: %s", str(e)[:120])
        return 0
    for s in stories:
        try:
            cl.story_download(int(s.pk), folder=pasta)
            baixados += 1
            _pausa()
        except Exception as e:
            logger.warning("[DL] falhou story %s: %s", getattr(s, 'pk', '?'), str(e)[:120])
    return baixados


def _baixar_destaques(cl, uid, pasta):
    baixados = 0
    try:
        highlights = cl.user_highlights(uid)
    except Exception as e:
        logger.warning("[DL] destaques indisponíveis: %s", str(e)[:120])
        return 0
    for h in highlights:
        try:
            info = cl.highlight_info(h.pk)
            itens = getattr(info, 'items', []) or []
        except Exception as e:
            logger.warning("[DL] falhou abrir destaque %s: %s", getattr(h, 'pk', '?'), str(e)[:120])
            continue
        for item in itens:
            try:
                cl.story_download(int(item.pk), folder=pasta)
                baixados += 1
                _pausa()
            except Exception as e:
                logger.warning("[DL] falhou item de destaque %s: %s", getattr(item, 'pk', '?'), str(e)[:120])
    return baixados


@shared_task(bind=True, soft_time_limit=3300, time_limit=3600)
def run_profile_download(self, job_id):
    """Executa um ProfileDownload: baixa o que foi pedido e monta o ZIP."""
    from .models import ProfileDownload

    job = ProfileDownload.objects.select_related('account').filter(id=job_id).first()
    if not job:
        return
    if not job.account:
        job.status = 'failed'
        job.error = 'Nenhuma conta selecionada para fazer a leitura do perfil.'
        job.finished_at = timezone.now()
        job.save()
        return

    job.status = 'running'
    job.progress_msg = 'Conectando à conta…'
    job.save(update_fields=['status', 'progress_msg'])

    base_dir = os.path.join(settings.MEDIA_ROOT, 'downloads')
    work_dir = os.path.join(base_dir, f'tmp_{job.id}')
    os.makedirs(work_dir, exist_ok=True)
    legendas = []

    def _andamento(msg, salvar_count=True):
        job.progress_msg = msg[:255]
        campos = ['progress_msg']
        if salvar_count:
            campos.append('media_count')
        job.save(update_fields=campos)

    try:
        cl = _cliente_logado(job.account)

        alvo = job.target_username
        _andamento(f'Localizando @{alvo}…', salvar_count=False)
        uid = cl.user_id_from_username(alvo)

        total = 0
        if job.want_feed or job.want_reels:
            _andamento('Baixando feed e reels…', salvar_count=False)
            n = _baixar_medias(cl, uid, job.amount or 0,
                               job.want_feed, job.want_reels, work_dir, legendas)
            total += n
            job.media_count = total
            _andamento(f'{total} itens do feed/reels baixados…')
        if job.want_stories:
            _andamento('Baixando stories atuais…')
            n = _baixar_stories(cl, uid, work_dir)
            total += n
            job.media_count = total
            _andamento(f'{total} itens baixados (stories incluídos)…')
        if job.want_highlights:
            _andamento('Baixando destaques…')
            n = _baixar_destaques(cl, uid, work_dir)
            total += n
            job.media_count = total
            _andamento(f'{total} itens baixados (destaques incluídos)…')

        _finalizar_zip(job, work_dir, legendas, base_dir)

    except SoftTimeLimitExceeded:
        # Estourou o tempo — empacota o que já veio em vez de perder tudo.
        logger.warning("[DL] job %s estourou o tempo — empacotando parcial", job.id)
        try:
            _finalizar_zip(job, work_dir, legendas, base_dir, parcial=True)
        except Exception as e:
            _falhar(job, f'Tempo esgotado e falha ao empacotar: {e}')
    except Exception as e:
        logger.exception("[DL] job %s falhou", job.id)
        _falhar(job, str(e))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _finalizar_zip(job, work_dir, legendas, base_dir, parcial=False):
    from .models import ProfileDownload

    # Junta as legendas num arquivo só, para repostar com o texto original.
    if legendas:
        with open(os.path.join(work_dir, 'legendas.txt'), 'w', encoding='utf-8') as fh:
            for nome, texto in legendas:
                fh.write(f'== {nome} ==\n{texto}\n\n')

    arquivos = []
    for raiz, _dirs, nomes in os.walk(work_dir):
        for nome in nomes:
            arquivos.append(os.path.join(raiz, nome))

    if not arquivos:
        _falhar(job, 'Nada foi baixado. O perfil pode ser privado, estar vazio '
                     'ou a conta usada não tem acesso a ele.')
        return

    os.makedirs(base_dir, exist_ok=True)
    zip_nome = f'{_slug(job.target_username)}_{job.id}.zip'
    zip_abs = os.path.join(base_dir, zip_nome)
    with zipfile.ZipFile(zip_abs, 'w', zipfile.ZIP_DEFLATED) as zf:
        for caminho in arquivos:
            zf.write(caminho, arcname=os.path.relpath(caminho, work_dir))

    job.zip_file.name = f'downloads/{zip_nome}'
    job.size_bytes = os.path.getsize(zip_abs)
    job.media_count = len([a for a in arquivos if not a.endswith('legendas.txt')])
    job.status = 'done'
    job.finished_at = timezone.now()
    if parcial:
        job.progress_msg = f'Parcial (tempo esgotado): {job.media_count} itens.'
    else:
        job.progress_msg = f'Concluído: {job.media_count} itens.'
    job.save()


def _falhar(job, msg):
    job.status = 'failed'
    job.error = (msg or 'Erro desconhecido.')[:2000]
    job.finished_at = timezone.now()
    job.save(update_fields=['status', 'error', 'finished_at'])
