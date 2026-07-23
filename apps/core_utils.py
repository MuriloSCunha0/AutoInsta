"""Utilitários compartilhados entre os apps."""
import os
import re
import unicodedata
from urllib.parse import quote


def nome_seguro(filename):
    """Devolve um nome de arquivo 100% ASCII e seguro para URL.

    Por que isso importa: a Meta BAIXA a mídia da URL que enviamos. Nomes com
    acento (`história.mp4`), espaços ou `#`/`?` fazem o downloader dela devolver
    status_code=ERROR sem explicar o motivo. Verificado em produção:
    mesmo vídeo com acento no nome = ERROR, com nome ASCII = FINISHED.
    """
    base, ext = os.path.splitext(filename or '')
    # "história" -> "historia" (remove os acentos, preserva a letra base)
    base = unicodedata.normalize('NFKD', base).encode('ascii', 'ignore').decode('ascii')
    ext = unicodedata.normalize('NFKD', ext).encode('ascii', 'ignore').decode('ascii')
    base = re.sub(r'[^A-Za-z0-9._-]+', '_', base).strip('._-')
    ext = re.sub(r'[^A-Za-z0-9.]+', '', ext)
    return (base or 'arquivo')[:180] + (ext or '')


def url_midia(site_url, media_url, relname):
    """Monta a URL pública da mídia com percent-encoding no caminho.

    Rede de segurança para os arquivos que já estão no disco com acento no
    nome: `história` vira `hist%C3%B3ria`, que a Meta consegue baixar.
    """
    caminho = f"{media_url}{relname}"
    return f"{site_url.rstrip('/')}{quote(caminho, safe='/')}"
