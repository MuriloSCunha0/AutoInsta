# -*- coding: utf-8 -*-
"""Renderização de texto sobre a imagem do Story (fase 2 do editor visual).

A API/engine não "digita" texto no Story — então queimamos o texto direto na
imagem antes de subir, na posição/cor/tamanho que o usuário escolheu no preview.
Só vale para STORY de IMAGEM (vídeo exigiria overlay de ffmpeg — fica p/ depois).
"""
import os
import textwrap

# Fontes candidatas (a imagem Docker instala fonts-dejavu-core).
_FONT_CANDIDATES = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    'C:\\Windows\\Fonts\\arialbd.ttf',
    'C:\\Windows\\Fonts\\arial.ttf',
]

# Largura do quadro de preview no front (px). O tamanho da fonte vem em px
# desse preview; escalamos para a largura real da imagem.
_PREVIEW_W = 190.0


def _achar_fonte(size):
    from PIL import ImageFont
    for caminho in _FONT_CANDIDATES:
        if os.path.exists(caminho):
            try:
                return ImageFont.truetype(caminho, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _hex_to_rgb(cor, padrao=(255, 255, 255)):
    cor = (cor or '').strip().lstrip('#')
    if len(cor) == 3:
        cor = ''.join(c * 2 for c in cor)
    if len(cor) != 6:
        return padrao
    try:
        return tuple(int(cor[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return padrao


def bake_story_text(src_path, *, text, color='#ffffff', bg='dark',
                    size_preview=28, x=0.5, y=0.45, dest_dir=None):
    """Queima `text` sobre a imagem em `src_path` e devolve o caminho do novo
    arquivo. Se algo falhar, devolve o caminho original (nunca quebra o publish).
    """
    text = (text or '').strip()
    if not text:
        return src_path

    try:
        from PIL import Image, ImageDraw
    except Exception:
        return src_path

    try:
        img = Image.open(src_path).convert('RGBA')
        W, H = img.size

        # Escala o tamanho do preview (190px) para a imagem real.
        font_size = max(12, int(float(size_preview) * (W / _PREVIEW_W)))
        font = _achar_fonte(font_size)

        # Quebra o texto para caber em ~80% da largura.
        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        max_w = W * 0.8
        # Estima chars por linha pela largura média do 'M'.
        try:
            char_w = draw.textlength('M', font=font) or (font_size * 0.6)
        except Exception:
            char_w = font_size * 0.6
        wrap_at = max(6, int(max_w / max(char_w, 1)))
        linhas = []
        for paragrafo in text.split('\n'):
            linhas.extend(textwrap.wrap(paragrafo, width=wrap_at) or [''])

        # Mede o bloco.
        line_h = font_size + max(4, int(font_size * 0.2))
        bloco_w = 0
        for ln in linhas:
            try:
                w = draw.textlength(ln, font=font)
            except Exception:
                w = len(ln) * char_w
            bloco_w = max(bloco_w, w)
        bloco_h = line_h * len(linhas)

        cx = int(x * W)
        cy = int(y * H)
        left = int(cx - bloco_w / 2)
        top = int(cy - bloco_h / 2)

        pad = max(8, int(font_size * 0.35))
        rgb = _hex_to_rgb(color)

        # Fundo opcional atrás do texto.
        if bg == 'dark':
            box_fill = (0, 0, 0, 140)
        elif bg == 'light':
            box_fill = (255, 255, 255, 210)
        else:
            box_fill = None
        if box_fill is not None:
            try:
                draw.rounded_rectangle(
                    [left - pad, top - pad, left + bloco_w + pad, top + bloco_h + pad],
                    radius=max(8, pad), fill=box_fill,
                )
            except Exception:
                draw.rectangle(
                    [left - pad, top - pad, left + bloco_w + pad, top + bloco_h + pad],
                    fill=box_fill,
                )

        # Sombra leve para legibilidade quando não há fundo.
        for i, ln in enumerate(linhas):
            ly = top + i * line_h
            try:
                lw = draw.textlength(ln, font=font)
            except Exception:
                lw = len(ln) * char_w
            lx = int(cx - lw / 2)
            if box_fill is None:
                draw.text((lx + 2, ly + 2), ln, font=font, fill=(0, 0, 0, 160))
            draw.text((lx, ly), ln, font=font, fill=rgb + (255,))

        final = Image.alpha_composite(img, overlay).convert('RGB')

        dest_dir = dest_dir or os.path.dirname(src_path)
        os.makedirs(dest_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(src_path))[0]
        out = os.path.join(dest_dir, f"{base}_story.jpg")
        final.save(out, 'JPEG', quality=92)
        return out
    except Exception as e:
        print(f"[story_render] falhou ({e}); usando imagem original")
        return src_path
