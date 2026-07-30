"""Utilitários compartilhados entre os apps.

CONTEXTO (não remover): a Meta não recebe o arquivo, ela BAIXA a mídia da URL
que enviamos. Nome de arquivo com acento (`história.mp4`), espaço ou `#`/`?`
faz o downloader dela devolver `status_code: ERROR` — sem dizer o motivo — e
NADA publica. Verificado em produção com o mesmo vídeo:

    nome com acento .............. ERROR
    mesmo vídeo, nome ASCII ...... FINISHED
    acento + URL percent-encoded . FINISHED

Por isso existem duas defesas independentes aqui:
  1. `nome_seguro`  — na gravação, para o problema não nascer;
  2. `url_segura`   — no envio, para os arquivos que já estão no disco.
"""
import logging
import os
import posixpath
import re
import tempfile
import unicodedata
from urllib.parse import quote, urlsplit, urlunsplit

from django.core.files.storage import FileSystemStorage

logger = logging.getLogger('engine')


def nome_seguro(filename):
    """Devolve um nome de arquivo 100% ASCII e seguro para URL."""
    base, ext = os.path.splitext(filename or '')
    # "história" -> "historia" (tira o acento, preserva a letra base)
    base = unicodedata.normalize('NFKD', base).encode('ascii', 'ignore').decode('ascii')
    ext = unicodedata.normalize('NFKD', ext).encode('ascii', 'ignore').decode('ascii')
    base = re.sub(r'[^A-Za-z0-9._-]+', '_', base).strip('._-')
    ext = re.sub(r'[^A-Za-z0-9.]+', '', ext)
    return (base or 'arquivo')[:180] + (ext or '')


def url_segura(url):
    """Percent-encoda o caminho da URL sem estragar o que já está encodado.

    `%` fica na lista de seguros de propósito: aplicar a função duas vezes não
    transforma `%C3%B3` em `%25C3%25B3`.
    """
    partes = urlsplit(url or '')
    return urlunsplit(partes._replace(path=quote(partes.path, safe="/%:@!$&'()*+,;=~")))


def url_midia(site_url, media_url, relname):
    """Monta a URL pública de uma mídia, já percent-encodada."""
    return url_segura(f"{(site_url or '').rstrip('/')}{media_url}{relname}")


def garantir_midia_local(fieldfile):
    """Devolve (caminho_local, eh_temporario) para o arquivo de um FileField.

    Numa máquina única (ou no painel), o arquivo já está no disco: devolve o
    próprio path e eh_temporario=False — ZERO custo, comportamento idêntico ao
    de antes.

    No "braço" (servidor dedicado de publicação, que NÃO tem o volume de mídia
    do painel), o arquivo não existe localmente. Aí baixamos da URL pública do
    painel para um temporário e devolvemos (tmp, True) — a engine/instagrapi
    precisa de um caminho local para subir os bytes. Quem chama deve apagar o
    temporário no fim.
    """
    from django.conf import settings

    if not fieldfile:
        return None, False

    # 1) Já está local? (painel / máquina única)
    try:
        local = fieldfile.path
        if local and os.path.exists(local):
            return local, False
    except (NotImplementedError, ValueError, AttributeError):
        pass

    # 2) Não está local (braço): baixa da URL pública do painel.
    import requests

    url = fieldfile.url
    if url.startswith('/'):
        site = (getattr(settings, 'SITE_URL', '') or '').rstrip('/')
        url = f"{site}{url}"
    url = url_segura(url)

    destino_dir = os.path.join(settings.MEDIA_ROOT, 'processed')
    os.makedirs(destino_dir, exist_ok=True)
    ext = os.path.splitext(fieldfile.name or '')[1] or ''
    fd, tmp = tempfile.mkstemp(suffix=ext, dir=destino_dir)
    os.close(fd)

    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, 'wb') as fh:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    fh.write(chunk)
    logger.info('garantir_midia_local: baixou %s -> %s', fieldfile.name, os.path.basename(tmp))
    return tmp, True


class MidiaStorage(FileSystemStorage):
    """Storage padrão do projeto: nenhum arquivo entra com nome problemático.

    É o ponto de estrangulamento — vale para todo FileField e para qualquer
    `default_storage.save()`, inclusive de código escrito depois disto. Sem
    ele, cada novo ponto de upload precisaria lembrar de sanear o nome.
    """

    def get_valid_name(self, name):  # usado pelos FileField (upload_to)
        return nome_seguro(name)

    def save(self, name, content=None, max_length=None):  # usado no save() direto
        if name is None and content is not None:
            name = getattr(content, 'name', None)
        if name:
            pasta, arquivo = posixpath.split(str(name).replace('\\', '/'))
            name = posixpath.join(pasta, nome_seguro(arquivo))
        return super().save(name, content, max_length)
