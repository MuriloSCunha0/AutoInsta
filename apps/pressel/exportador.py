"""Gera o HTML final da pressel — um arquivo ÚNICO, pronto para hospedar.

Por que data URI e não link para as imagens: o usuário sobe o arquivo no
Netlify/Vercel arrastando UM arquivo. Se as imagens apontassem para o nosso
domínio, a página dele quebraria no dia em que o painel saísse do ar, e ainda
entregaria de onde ela veio. Embutidas, o .html é autossuficiente.

Para o arquivo não ficar gigante, cada imagem é reduzida e recomprimida antes
de virar base64 (base64 já engorda ~33% por si só).
"""
import base64
import io
import logging

logger = logging.getLogger('apps.pressel')

# Largura máxima por papel da imagem. O fundo aparece desfocado e em escala
# 1.15, então não precisa de resolução alta — é o que mais pesaria.
LARGURAS = {
    'fundo': 1000,
    'perfil': 400,
    'btn1': 120,
    'btn2': 120,
    'card1': 700,
    'card2': 700,
    'card3': 700,
    'card4': 700,
}
QUALIDADE = 80


def _para_data_uri(fieldfile, largura_max):
    """Lê a imagem, reduz, recomprime e devolve `data:image/...;base64,...`.

    Best-effort: se qualquer coisa falhar, devolve None e a página sai sem
    aquela imagem em vez de estourar o download inteiro.
    """
    if not fieldfile:
        return None
    try:
        from PIL import Image

        with fieldfile.open('rb') as fh:
            img = Image.open(fh)
            img.load()

        # PNG/WebP com transparência viram PNG; o resto vira JPEG (bem menor).
        tem_alpha = img.mode in ('RGBA', 'LA', 'P')
        if img.width > largura_max:
            altura = round(img.height * largura_max / img.width)
            img = img.resize((largura_max, altura), Image.LANCZOS)

        buf = io.BytesIO()
        if tem_alpha:
            img.convert('RGBA').save(buf, format='PNG', optimize=True)
            mime = 'image/png'
        else:
            img.convert('RGB').save(buf, format='JPEG', quality=QUALIDADE, optimize=True)
            mime = 'image/jpeg'
        b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        return f'data:{mime};base64,{b64}'
    except Exception as e:
        logger.warning('pressel: não consegui embutir %s: %s',
                       getattr(fieldfile, 'name', '?'), e)
        return None


def imagens_embutidas(pressel):
    """{'fundo': 'data:...', 'perfil': 'data:...', ...} para o template."""
    return {
        chave: _para_data_uri(campo, LARGURAS.get(chave, 700))
        for chave, campo in pressel.imagens.items()
    }


def imagens_por_url(pressel, request=None):
    """Mesma forma, mas com a URL do MEDIA — usado na PRÉVIA do painel.

    A prévia não embute nada: além de ser mais rápida, deixa o editor
    responder na hora quando o usuário troca uma foto.
    """
    saida = {}
    for chave, campo in pressel.imagens.items():
        if not campo:
            saida[chave] = None
            continue
        try:
            url = campo.url
        except ValueError:
            saida[chave] = None
            continue
        saida[chave] = request.build_absolute_uri(url) if request else url
    return saida


def _cards_com_src(pressel, img):
    """Junta cada card com o src já resolvido (data URI ou URL)."""
    cards = []
    for i in (1, 2, 3, 4):
        imagem = getattr(pressel, f'card{i}_imagem')
        texto = getattr(pressel, f'card{i}_texto')
        if not imagem and not texto:
            continue
        cards.append({'src': img.get(f'card{i}'), 'texto': texto})
    return cards


def render_pressel(pressel, img):
    """Renderiza a página com o mapa de imagens que vier (data URI ou URL)."""
    from django.template.loader import render_to_string
    return render_to_string('pressel/pagina.html', {
        'p': pressel,
        'img': img,
        'cards': _cards_com_src(pressel, img),
    })


def html_para_download(pressel):
    """O arquivo final: um HTML autossuficiente."""
    return render_pressel(pressel, imagens_embutidas(pressel))


def html_para_previa(pressel, request=None):
    return render_pressel(pressel, imagens_por_url(pressel, request))
