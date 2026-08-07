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


def midia_local_por_nome(relname):
    """Caminho LOCAL para uma mídia dado o nome relativo ao MEDIA_ROOT.

    Igual a garantir_midia_local, mas recebe o NOME (ex.: 'profile_pics/x.jpg')
    em vez do FileField — usado na edição em massa de perfil (a foto é salva no
    painel e o braço precisa dela local). Retorna (caminho, eh_temporario).
    """
    from django.conf import settings

    if not relname:
        return None, False
    local = os.path.join(settings.MEDIA_ROOT, relname.replace('/', os.sep))
    if os.path.exists(local):
        return local, False

    import requests
    site = (getattr(settings, 'SITE_URL', '') or '').rstrip('/')
    media_url = getattr(settings, 'MEDIA_URL', '/media/')
    url = url_segura(f"{site}{media_url}{relname}")
    destino = os.path.join(settings.MEDIA_ROOT, 'processed')
    os.makedirs(destino, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(relname)[1] or '.jpg', dir=destino)
    os.close(fd)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, 'wb') as fh:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    fh.write(chunk)
    logger.info('midia_local_por_nome: baixou %s', relname)
    return tmp, True


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


def e_madrugada(quando):
    """O horário cai na faixa de madrugada (hora LOCAL do usuário)?

    Publicar de madrugada é o sinal mais robótico que existe — conta real não
    posta às 4h todo dia. Medido em produção: a curva de publicações por hora
    estava plana nas 24h, com tanto post às 4h quanto ao meio-dia.

    Não bloqueia nada; serve para AVISAR no composer e ao criar a fila.
    """
    from django.conf import settings
    from django.utils import timezone

    if not quando:
        return False
    ini = getattr(settings, 'MADRUGADA_INI', 0)
    fim = getattr(settings, 'MADRUGADA_FIM', 5)
    try:
        hora = timezone.localtime(quando).hour if timezone.is_aware(quando) else quando.hour
    except (AttributeError, ValueError):
        return False
    if ini <= fim:
        return ini <= hora < fim
    # Faixa que cruza a meia-noite (ex.: 22 -> 5).
    return hora >= ini or hora < fim


def aviso_madrugada(horarios):
    """Devolve um aviso (str) se algum horário da lista cair na madrugada.

    Recebe uma lista de datetimes (os horários que a fila vai usar) e devolve
    None quando está tudo bem — assim a view só precisa repassar o texto.
    """
    from django.conf import settings

    quantos = sum(1 for h in horarios if e_madrugada(h))
    if not quantos:
        return None
    ini = getattr(settings, 'MADRUGADA_INI', 0)
    fim = getattr(settings, 'MADRUGADA_FIM', 5)
    return (
        f'Atenção: {quantos} publicação(ões) ficaram entre {ini:02d}h e {fim:02d}h. '
        'Postar de madrugada todo dia é um dos sinais que o Instagram mais usa '
        'para identificar automação — conta real não posta nesse horário. '
        'Se der, concentre a fila entre 07h e 23h.'
    )


def msg_meta_amigavel(msg):
    """Traduz o erro CRU da Meta (JSON com 'OAuthException' etc.) numa frase
    clara pro usuário — o dict cru assusta e confunde no card."""
    m = (msg or '').lower()
    # LIMITE PRIMEIRO. A Meta manda os erros de limite (codes 4/9/17/32/613) com
    # o MESMO `type: OAuthException` de um token inválido. Com o teste de token
    # antes — casando com 'oauthexception' solto — uma conta apenas LIMITADA
    # recebia "veja se a conta está SUSPENSA" e o dono achava que tinha caído.
    # Foi a confusão relatada pelo usuário iorio: 3 contas em 'error' cujo token
    # respondia HTTP 200 na Graph API, só com a cota em 50/100.
    from apps.publisher.tasks import _e_rate_limit, _e_restricao_temporaria
    if _e_rate_limit(msg):
        return ('Limite de publicações da Meta atingido — a conta está OK, só '
                'precisa esperar. A fila retoma sozinha depois do cooldown.')
    # Restrição temporária da conta (code 25). Igual ao limite: NÃO é queda, o
    # token está bom, não reconectar nada. Vem antes do teste de token para não
    # cair no "veja se está SUSPENSA".
    if _e_restricao_temporaria(msg):
        # Não afirmar "restringiu a publicação": a Meta manda esse mesmo erro
        # quando a restrição é só de MENSAGENS e a conta posta normal. Dizemos o
        # que ela de fato disse, e para onde o usuário olha para descobrir o tipo.
        return ('A Meta recusou dizendo que a conta está restrita, sem informar o '
                'tipo (pode ser só de mensagens). A conta está OK — não precisa '
                'reconectar; ela tenta de novo sozinha. Se repetir, veja "Status '
                'da conta" no instagram.com.')
    if ('cannot access the app' in m or 'log in to www.instagram.com' in m
            or 'error validating access token' in m or "'code': 190" in m):
        return ('Entre no instagram.com com esta conta e veja o que aparece: '
                'se a conta estiver SUSPENSA, recorra ali mesmo (o token novo só '
                'funciona depois que ela voltar); se estiver normal, gere um token '
                'novo e cole em "Atualizar token".')
    if 'does not exist' in m or 'unsupported post request' in m:
        return 'ID da conta desatualizado — o sistema corrige e republica sozinho.'
    # Fallback: tira o dict cru e devolve uma frase curta e legível.
    limpo = (msg or '').replace('{', '').replace('}', '').replace("'", '').strip()
    if limpo.lower().startswith('meta:'):
        limpo = limpo[5:].strip()
    return limpo[:160] or 'Falha ao publicar na Meta.'


def enviar_midia_para_painel(local_path, relname):
    """Envia um arquivo de mídia PROCESSADO (limpo/gerado no braço) para o PAINEL,
    para a Graph API (oficial) baixá-lo pela URL pública — a Meta baixa a mídia
    por URL, e o arquivo processado só existe no disco do braço.

    No painel / máquina única (sem PAINEL_MEDIA_UPLOAD_URL) é no-op: o arquivo já
    está onde o Caddy serve. Retorna True se enviou, False se não havia destino.
    Levanta em falha de rede/servidor para o chamador decidir (revezar/erro).
    """
    from django.conf import settings
    url = (getattr(settings, 'PAINEL_MEDIA_UPLOAD_URL', '') or '').strip()
    if not url:
        return False  # máquina única/painel: nada a enviar
    import requests
    token = getattr(settings, 'MEDIA_UPLOAD_TOKEN', '') or ''
    with open(local_path, 'rb') as fh:
        r = requests.post(
            url,
            data={'relname': relname},
            files={'file': fh},
            headers={'X-Upload-Token': token},
            timeout=180,
        )
    r.raise_for_status()
    logger.info('enviar_midia_para_painel: %s -> painel OK', relname)
    return True


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
