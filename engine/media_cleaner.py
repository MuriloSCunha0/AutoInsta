"""Limpeza e diversificação de mídia antes de publicar.

Por que existe: quando o MESMO arquivo é enviado por várias contas, o
Instagram consegue correlacioná-las (mesmo hash / mesmo fingerprint) e isso
aumenta o risco de bloqueio. Aqui geramos uma cópia diferente por conta.

Modos:
  - none  : não mexe no arquivo (mais rápido, sem custo de CPU)
  - light : remuxa mudando só o HASH (stream copy, quase instantâneo).
            Mantém os metadados originais e a qualidade intacta.
  - ultra : remove TODOS os metadados (câmera, GPS, software, EXIF, encoder)
            e aplica micro-variações visuais determinísticas por conta
            (brilho/saturação/crop), mudando também o fingerprint perceptual.

Tudo é best-effort: se o ffmpeg não estiver disponível ou falhar, devolvemos
o arquivo original em vez de quebrar a publicação.
"""

import logging
import os
import random
import subprocess
import uuid

logger = logging.getLogger('engine')

FFMPEG = 'ffmpeg'
TIMEOUT_S = 300


def ffmpeg_disponivel():
    try:
        r = subprocess.run([FFMPEG, '-version'], capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def _saida(src_path, dest_dir):
    base = dest_dir or os.path.join(os.path.dirname(src_path), 'processed')
    os.makedirs(base, exist_ok=True)
    ext = os.path.splitext(src_path)[1] or '.mp4'
    return os.path.join(base, f"clean_{uuid.uuid4().hex[:12]}{ext}")


def _rodar(cmd):
    proc = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_S)
    if proc.returncode != 0:
        erro = (proc.stderr or b'').decode('utf-8', 'ignore')[-600:]
        raise RuntimeError(f"ffmpeg falhou: {erro}")


def _cmd_light(src, dst, rng):
    """Só muda o hash: copia os streams e injeta um comentário aleatório.
    Sem recodificar -> rápido e sem perda de qualidade."""
    marca = uuid.UUID(int=rng.getrandbits(128)).hex
    return [
        FFMPEG, '-y', '-i', src,
        '-c', 'copy',
        '-metadata', f'comment={marca}',
        '-movflags', '+faststart',
        dst,
    ]


def _cmd_ultra(src, dst, rng):
    """Remove todos os metadados e aplica micro-variações visuais.

    As variações são pequenas o bastante para não serem percebidas, mas
    mudam o fingerprint perceptual entre as contas.
    """
    brilho = round(rng.uniform(-0.015, 0.015), 4)      # ~1.5%
    saturacao = round(rng.uniform(0.97, 1.03), 4)      # ~3%
    contraste = round(rng.uniform(0.98, 1.02), 4)
    corte = rng.choice([2, 4, 6])                       # px removidos das bordas

    # Corta alguns pixels e volta ao tamanho original (mantém a proporção).
    vf = (
        f"crop=iw-{corte}:ih-{corte},"
        f"scale=iw+{corte}:ih+{corte},"
        f"eq=brightness={brilho}:saturation={saturacao}:contrast={contraste}"
    )

    return [
        FFMPEG, '-y', '-i', src,
        '-map_metadata', '-1',          # remove metadados do contêiner
        '-map_metadata:s:v', '-1',      # ...e os do stream de vídeo
        '-map_metadata:s:a', '-1',      # ...e os do stream de áudio
        '-map_chapters', '-1',
        '-vf', vf,
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '128k',
        # -bitexact remove as tags "encoder"/versão que o ffmpeg carimbaria.
        # Sem isso, TODO arquivo nosso sairia com encoder="Lavc libx264" —
        # um marcador comum que ligaria as contas entre si.
        '-bitexact',
        # handler_name padrão ("VideoHandler"/"SoundHandler") também é um
        # marcador constante: zeramos.
        '-metadata:s:v', 'handler_name=',
        '-metadata:s:a', 'handler_name=',
        '-movflags', '+faststart',
        dst,
    ]


def limpar_video(src_path, mode='light', seed=None, dest_dir=None):
    """Gera uma cópia processada do vídeo.

    Retorna o caminho do arquivo processado, ou o ORIGINAL se o modo for
    'none', se o ffmpeg não existir ou se o processamento falhar.
    """
    if mode not in ('light', 'ultra'):
        return src_path

    if not os.path.exists(src_path):
        logger.warning('limpar_video: arquivo inexistente %s', src_path)
        return src_path

    if not ffmpeg_disponivel():
        logger.warning('limpar_video: ffmpeg indisponível, publicando o original')
        return src_path

    # Seed determinística por conta/post: mesma conta -> mesmo tratamento,
    # contas diferentes -> arquivos diferentes.
    rng = random.Random(str(seed) if seed is not None else uuid.uuid4().hex)
    dst = _saida(src_path, dest_dir)

    try:
        cmd = _cmd_light(src_path, dst, rng) if mode == 'light' else _cmd_ultra(src_path, dst, rng)
        _rodar(cmd)
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            logger.info('limpar_video: modo=%s -> %s', mode, os.path.basename(dst))
            return dst
        raise RuntimeError('saída vazia')
    except Exception as e:
        logger.warning('limpar_video falhou (modo=%s): %s — publicando o original', mode, e)
        try:
            if os.path.exists(dst):
                os.remove(dst)
        except Exception:
            pass
        return src_path
