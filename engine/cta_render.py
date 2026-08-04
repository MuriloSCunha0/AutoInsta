# -*- coding: utf-8 -*-
"""Gerador de arte de CTA — imagem 9:16 com adesivo no estilo do Instagram.

O que resolve: para levar tráfego para o link, o story precisa de uma chamada
visual. O adesivo nativo só existe quando a conta publica pela engine (sessão);
pela API oficial não dá para anexar figurinha. Aqui a chamada é DESENHADA na
imagem, então funciona em qualquer conta, inclusive as só-token.

Tudo é Pillow puro (já é dependência) — sem browser, sem serviço externo.

IMPORTANTE: este módulo é a ÚNICA fonte da arte. A tela mostra o PNG que sai
daqui em vez de imitar o layout em CSS — foi assim que o editor de Story passou
a divergir do resultado (preview de 170px contra render de 190px).
"""
import os
import re
import unicodedata

from engine.story_render import _achar_fonte, _hex_to_rgb

# 1080x1920 é o tamanho nativo de story/reel.
LARGURA, ALTURA = 1080, 1920

TIPOS = (
    ('link', 'Botão de link (CLIQUE AQUI)'),
    ('enquete', 'Enquete (2 opções)'),
    ('pergunta', 'Caixa de perguntas'),
    ('contagem', 'Contagem regressiva'),
    ('nenhum', 'Só o texto (sem adesivo)'),
)


def _fonte(px, bold=True):
    return _achar_fonte(max(10, int(px)))


# ── Emoji ────────────────────────────────────────────────────────────────────
# A DejaVu (fonte de texto do servidor) não tem NENHUM emoji: cada um sairia
# como o retângulo do .notdef. A saída é desenhar o emoji com uma fonte COLORIDA
# separada e colá-lo na linha — nenhuma fonte única cobre texto + emoji.
#
# NotoColorEmoji é bitmap (CBDT): o Pillow só a abre nos tamanhos de strike que
# o arquivo tem — na prática 109px. Por isso renderizamos sempre nesse tamanho
# e reduzimos para a altura da linha. A fonte vem do pacote
# `fonts-noto-color-emoji`, instalado no Dockerfile.
_EMOJI_FONTS = [
    '/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf',        # Debian/Ubuntu
    '/usr/share/fonts/truetype/noto/NotoColorEmoji-Regular.ttf',
    'C:\\Windows\\Fonts\\seguiemj.ttf',                         # Windows (dev)
]
_EMOJI_STRIKE = 109

# Blocos onde moram os emoji. Não precisa ser exaustivo: o que escapar daqui
# cai no filtro de glifo ausente e é removido em vez de virar quadradinho.
_RE_EMOJI = re.compile(
    '(?:[\U0001F300-\U0001FAFF\U0001F000-\U0001F0FF\U0001F900-\U0001F9FF'
    '\U00002600-\U000027BF\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF]'
    '[\U0000FE00-\U0000FE0F\u200d]*)+'
)

_cache_fonte_emoji = {}
_cache_emoji_img = {}


def _fonte_emoji():
    """A fonte de emoji colorido, ou None se a máquina não tiver nenhuma."""
    if 'f' in _cache_fonte_emoji:
        return _cache_fonte_emoji['f']
    from PIL import ImageFont
    achada = None
    for caminho in _EMOJI_FONTS:
        if not os.path.exists(caminho):
            continue
        # A bitmap só abre no tamanho da strike; a vetorial (Windows) abre em
        # qualquer um. Tentamos do maior para o menor.
        for tamanho in (_EMOJI_STRIKE, 96, 72, 64, 48, 32):
            try:
                achada = ImageFont.truetype(caminho, tamanho)
                break
            except Exception:
                continue
        if achada:
            break
    _cache_fonte_emoji['f'] = achada
    return achada


def tem_suporte_a_emoji():
    """A tela usa isto para avisar quando o emoji não vai sair na arte."""
    return _fonte_emoji() is not None


def _fatiar(texto):
    """Quebra em [(é_emoji, trecho), ...] preservando a ordem."""
    saida, pos, texto = [], 0, texto or ''
    for m in _RE_EMOJI.finditer(texto):
        if m.start() > pos:
            saida.append((False, texto[pos:m.start()]))
        saida.append((True, m.group()))
        pos = m.end()
    if pos < len(texto):
        saida.append((False, texto[pos:]))
    return saida


def _img_emoji(trecho, altura):
    """Desenha o emoji na altura pedida e devolve um RGBA (ou None)."""
    chave = (trecho, int(altura))
    if chave in _cache_emoji_img:
        return _cache_emoji_img[chave]

    from PIL import Image, ImageDraw
    fonte = _fonte_emoji()
    resultado = None
    if fonte:
        try:
            lado = _EMOJI_STRIKE * 3
            tmp = Image.new('RGBA', (lado * max(1, len(trecho)), lado), (0, 0, 0, 0))
            d = ImageDraw.Draw(tmp)
            # embedded_color=True é o que traz o emoji COLORIDO — sem isso a
            # bitmap da Noto sai chapada na cor do texto.
            d.text((0, 0), trecho, font=fonte, embedded_color=True)
            caixa = tmp.getbbox()
            if caixa:
                tmp = tmp.crop(caixa)
                escala = altura / tmp.height
                resultado = tmp.resize(
                    (max(1, round(tmp.width * escala)), max(1, int(altura))),
                    Image.LANCZOS)
        except Exception:
            resultado = None

    _cache_emoji_img[chave] = resultado
    return resultado


def _desenho_do_char(ch, font):
    """Os pixels que a fonte produz para este caractere.

    `font.getmask()` devolve um ImagingCore, que não tem `tobytes()`. Então
    desenhamos num bitmap pequeno e comparamos os bytes — só API pública.
    """
    from PIL import Image, ImageDraw
    lado = max(8, int(font.size * 2))
    im = Image.new('L', (lado, lado), 0)
    ImageDraw.Draw(im).text((0, 0), ch, font=font, fill=255)
    return im.tobytes()


def _assinatura_notdef(font):
    """Como esta fonte desenha um caractere que ela NÃO tem.

    Não dá para detectar glifo ausente por bbox: a fonte desenha `.notdef` (o
    retângulo/tofu), que tem bbox como qualquer outro. O jeito confiável é
    comparar o desenho com o de um caractere que certamente não existe — usamos
    a Área de Uso Privado (U+F8FF). Todo glifo ausente renderiza igual a esse.
    """
    try:
        return _desenho_do_char('\uf8ff', font)
    except Exception:
        return None


def _limpar_incompativel(texto, font):
    """Tira o que a fonte de TEXTO não desenha — sem tocar nos emoji.

    Emoji são desenhados à parte (ver _img_emoji), então permanecem no texto.
    O que sobrar e a fonte não tiver viraria quadradinho na arte do cliente.
    """
    if not texto:
        return ''
    notdef = _assinatura_notdef(font)
    saida = []
    for e_emoji, trecho in _fatiar(texto):
        if e_emoji:
            saida.append(trecho)
            continue
        for ch in trecho:
            if ch.isspace():
                saida.append(ch)
                continue
            # Controle/formatação (ZWJ solto, variation selector) não desenham
            # nada e só atrapalham a medição.
            if unicodedata.category(ch) in ('Cc', 'Cf'):
                continue
            if notdef:
                try:
                    if _desenho_do_char(ch, font) == notdef:
                        continue
                except Exception:
                    pass
            saida.append(ch)
    return ''.join(saida).strip()


# ── Medição e desenho de texto COM emoji ─────────────────────────────────────

def _largura(draw, texto, font):
    """Largura da linha contando texto e emoji."""
    total = 0
    for e_emoji, trecho in _fatiar(texto or ''):
        if e_emoji:
            im = _img_emoji(trecho, font.size)
            total += im.width if im else 0
            continue
        try:
            total += draw.textlength(trecho, font=font)
        except Exception:
            total += len(trecho) * font.size * 0.6
    return total


def _escrever(camada, draw, x, y, texto, font, cor, sombra=False):
    """Escreve a linha em (x, y), colando os emoji entre os trechos de texto."""
    for e_emoji, trecho in _fatiar(texto or ''):
        if e_emoji:
            im = _img_emoji(trecho, font.size)
            if im is not None and camada is not None:
                # Alinha pela base do texto (o emoji sobe um pouco).
                camada.alpha_composite(im, (int(x), int(y + font.size * 0.06)))
                x += im.width
            continue
        if sombra:
            draw.text((x + 3, y + 3), trecho, font=font, fill=(0, 0, 0, 150))
        draw.text((x, y), trecho, font=font, fill=cor)
        try:
            x += draw.textlength(trecho, font=font)
        except Exception:
            x += len(trecho) * font.size * 0.6
    return x


def _texto_centralizado(camada, draw, cx, y, texto, font, cor, sombra=False):
    w = _largura(draw, texto, font)
    _escrever(camada, draw, cx - w / 2, y, texto, font, cor, sombra)
    return w


def _quebrar(draw, texto, font, largura_max):
    """Quebra o texto para caber na largura, medindo de verdade."""
    palavras = (texto or '').split()
    if not palavras:
        return ['']
    linhas, atual = [], palavras[0]
    for p in palavras[1:]:
        teste = f'{atual} {p}'
        if _largura(draw, teste, font) <= largura_max:
            atual = teste
        else:
            linhas.append(atual)
            atual = p
    linhas.append(atual)
    return linhas


def _fundo(base_path, escurecer):
    """Abre a imagem base e a encaixa em 1080x1920 (cover), com escurecimento."""
    from PIL import Image, ImageEnhance

    if base_path and os.path.exists(base_path):
        img = Image.open(base_path).convert('RGB')
        # cover: preenche o quadro sem distorcer, cortando o excesso
        escala = max(LARGURA / img.width, ALTURA / img.height)
        novo = (max(1, round(img.width * escala)), max(1, round(img.height * escala)))
        img = img.resize(novo, Image.LANCZOS)
        esq = (img.width - LARGURA) // 2
        topo = (img.height - ALTURA) // 2
        img = img.crop((esq, topo, esq + LARGURA, topo + ALTURA))
    else:
        img = Image.new('RGB', (LARGURA, ALTURA), (14, 14, 20))

    if escurecer:
        img = ImageEnhance.Brightness(img).enhance(max(0.15, 1.0 - float(escurecer)))
    return img.convert('RGBA')


# ── Adesivos ─────────────────────────────────────────────────────────────────
# Cada função desenha e devolve a ALTURA ocupada.

def _sticker_link(camada, draw, cx, topo, texto, escala):
    """Pílula branca com o texto — o visual do adesivo de link."""
    fonte = _fonte(46 * escala)
    texto = (_limpar_incompativel(texto, fonte) or 'CLIQUE AQUI').upper()
    tw = _largura(draw, texto, fonte)
    padx, pady = 46 * escala, 26 * escala
    icone_w = 44 * escala
    larg = tw + padx * 2 + icone_w
    alt = fonte.size + pady * 2
    x0, y0 = cx - larg / 2, topo
    raio = alt / 2

    draw.rounded_rectangle([x0, y0, x0 + larg, y0 + alt], radius=raio,
                           fill=(255, 255, 255, 245))
    # Elo de corrente simplificado (o ícone do adesivo de link).
    ix, iy = x0 + padx * 0.75, y0 + alt / 2
    r = 11 * escala
    for dx in (-r * 0.9, r * 0.9):
        draw.ellipse([ix + dx - r, iy - r * 0.62, ix + dx + r, iy + r * 0.62],
                     outline=(20, 20, 20, 255), width=max(2, int(4 * escala)))
    draw.line([ix - r * 0.5, iy, ix + r * 0.5, iy],
              fill=(20, 20, 20, 255), width=max(2, int(4 * escala)))

    _escrever(camada, draw, x0 + padx + icone_w, y0 + pady, texto, fonte,
              (20, 20, 20, 255))
    return alt


def _sticker_enquete(camada, draw, cx, topo, pergunta, opcao_a, opcao_b, escala):
    """Caixa branca com a pergunta em cima e duas opções lado a lado."""
    f_perg = _fonte(42 * escala)
    f_op = _fonte(46 * escala)
    pergunta = _limpar_incompativel(pergunta, f_perg) or 'Você quer ver?'
    opcao_a = _limpar_incompativel(opcao_a, f_op) or 'SIM'
    opcao_b = _limpar_incompativel(opcao_b, f_op) or 'CLARO'

    larg = 760 * escala
    pad = 34 * escala
    linhas = _quebrar(draw, pergunta, f_perg, larg - pad * 2)
    alt_perg = len(linhas) * (f_perg.size + 12 * escala)
    alt_op = f_op.size + 40 * escala
    alt = pad + alt_perg + alt_op + pad * 0.5
    x0, y0 = cx - larg / 2, topo

    draw.rounded_rectangle([x0, y0, x0 + larg, y0 + alt], radius=28 * escala,
                           fill=(255, 255, 255, 240))
    y = y0 + pad
    for ln in linhas:
        _texto_centralizado(camada, draw, cx, y, ln, f_perg, (20, 20, 20, 255))
        y += f_perg.size + 12 * escala

    meio_y0 = y + 6 * escala
    meio_y1 = y0 + alt - pad * 0.5
    draw.line([cx, meio_y0, cx, meio_y1], fill=(220, 220, 220, 255),
              width=max(2, int(3 * escala)))
    cy_op = meio_y0 + (meio_y1 - meio_y0) / 2 - f_op.size / 2
    _texto_centralizado(camada, draw, x0 + larg * 0.25, cy_op, opcao_a, f_op,
                        (20, 20, 20, 255))
    _texto_centralizado(camada, draw, x0 + larg * 0.75, cy_op, opcao_b, f_op,
                        (20, 20, 20, 255))
    return alt


def _sticker_pergunta(camada, draw, cx, topo, pergunta, placeholder, escala):
    """Caixa de perguntas: título + campo de resposta."""
    f_tit = _fonte(44 * escala)
    f_resp = _fonte(38 * escala)
    pergunta = _limpar_incompativel(pergunta, f_tit) or 'Me pergunta o que quiser'
    placeholder = _limpar_incompativel(placeholder, f_resp) or 'Responder...'

    larg = 780 * escala
    pad = 34 * escala
    linhas = _quebrar(draw, pergunta, f_tit, larg - pad * 2)
    alt_tit = len(linhas) * (f_tit.size + 12 * escala)
    alt_campo = f_resp.size + 34 * escala
    alt = pad + alt_tit + 16 * escala + alt_campo + pad
    x0, y0 = cx - larg / 2, topo

    draw.rounded_rectangle([x0, y0, x0 + larg, y0 + alt], radius=28 * escala,
                           fill=(255, 255, 255, 240))
    y = y0 + pad
    for ln in linhas:
        _texto_centralizado(camada, draw, cx, y, ln, f_tit, (20, 20, 20, 255))
        y += f_tit.size + 12 * escala

    y += 16 * escala
    draw.rounded_rectangle([x0 + pad, y, x0 + larg - pad, y + alt_campo],
                           radius=alt_campo / 2, fill=(238, 238, 238, 255))
    _texto_centralizado(camada, draw, cx, y + 17 * escala, placeholder, f_resp,
                        (140, 140, 140, 255))
    return alt


def _sticker_contagem(camada, draw, cx, topo, titulo, tempo, escala):
    """Contagem regressiva: título pequeno em cima do tempo grande."""
    f_tit = _fonte(38 * escala)
    f_tempo = _fonte(88 * escala)
    titulo = (_limpar_incompativel(titulo, f_tit) or 'ACABA EM').upper()
    tempo = _limpar_incompativel(tempo, f_tempo) or '00:30:00'

    larg = max(_largura(draw, tempo, f_tempo),
               _largura(draw, titulo, f_tit)) + 120 * escala
    pad = 30 * escala
    alt = pad + f_tit.size + 14 * escala + f_tempo.size + pad
    x0, y0 = cx - larg / 2, topo

    draw.rounded_rectangle([x0, y0, x0 + larg, y0 + alt], radius=28 * escala,
                           fill=(255, 255, 255, 240))
    _texto_centralizado(camada, draw, cx, y0 + pad, titulo, f_tit, (120, 120, 120, 255))
    _texto_centralizado(camada, draw, cx, y0 + pad + f_tit.size + 14 * escala,
                        tempo, f_tempo, (20, 20, 20, 255))
    return alt


def gerar_cta(base_path=None, *, tipo='link', titulo='', titulo_cor='#ffffff',
              titulo_tamanho=72, titulo_y=0.16, sticker_texto='',
              opcao_a='', opcao_b='', sticker_y=0.62, sticker_escala=1.0,
              escurecer=0.25, destino=None):
    """Monta a arte e devolve o caminho do JPG gerado.

    `titulo_y` / `sticker_y` são relativos (0..1) — a posição vertical dentro
    do quadro 9:16. O horizontal é sempre centralizado, que é como o adesivo
    nativo aparece na esmagadora maioria dos stories.
    """
    from PIL import Image, ImageDraw

    img = _fundo(base_path, escurecer)
    camada = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(camada)
    cx = LARGURA / 2

    # ── Título ───────────────────────────────────────────────────────────
    if (titulo or '').strip():
        f_tit = _fonte(titulo_tamanho)
        limpo = _limpar_incompativel(titulo, f_tit)
        cor = _hex_to_rgb(titulo_cor) + (255,)
        linhas = []
        for paragrafo in limpo.split('\n'):
            linhas.extend(_quebrar(draw, paragrafo, f_tit, LARGURA * 0.86))
        y = ALTURA * float(titulo_y)
        for ln in linhas:
            _texto_centralizado(camada, draw, cx, y, ln, f_tit, cor, sombra=True)
            y += f_tit.size * 1.25

    # ── Adesivo ──────────────────────────────────────────────────────────
    esc = max(0.4, min(float(sticker_escala or 1), 2.0))
    topo = ALTURA * float(sticker_y)
    if tipo == 'link':
        _sticker_link(camada, draw, cx, topo, sticker_texto, esc)
    elif tipo == 'enquete':
        _sticker_enquete(camada, draw, cx, topo, sticker_texto, opcao_a, opcao_b, esc)
    elif tipo == 'pergunta':
        _sticker_pergunta(camada, draw, cx, topo, sticker_texto, opcao_a, esc)
    elif tipo == 'contagem':
        _sticker_contagem(camada, draw, cx, topo, sticker_texto, opcao_a, esc)

    final = Image.alpha_composite(img, camada).convert('RGB')

    destino = destino or os.path.join(os.getcwd(), 'cta.jpg')
    os.makedirs(os.path.dirname(destino) or '.', exist_ok=True)
    final.save(destino, 'JPEG', quality=92)
    return destino
