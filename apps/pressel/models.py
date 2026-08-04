"""Construtor de pressel (página de links / pré-venda).

A ESTRUTURA e o CSS são fixos — vieram do modelo aprovado pelo usuário. Aqui só
guardamos o que é editável: imagens (com nível de desfoque), links e textos.

Por que os campos são granulares em vez de um "HTML livre": a página exportada
tem que sair sempre com a mesma estrutura. Deixar o usuário editar HTML abriria
espaço para quebrar o layout e para injetar script na página que ele publica.
"""
from django.db import models

from apps.accounts.models import User


class Pressel(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pressels')
    nome = models.CharField(
        max_length=120,
        help_text='Só para você achar depois. Não aparece na página.')

    # ── Página ───────────────────────────────────────────────────────────────
    titulo_pagina = models.CharField(
        max_length=120, default='Conteudo Privado',
        help_text='Aparece na aba do navegador.')

    # ── Fundo ────────────────────────────────────────────────────────────────
    imagem_fundo = models.ImageField(upload_to='pressel/', max_length=500,
                                     blank=True, null=True)
    # O desfoque do fundo é o que dá o clima da página; deixamos ajustável.
    desfoque_fundo = models.IntegerField(
        default=12, help_text='Desfoque do fundo, em pixels (0 = nítido).')
    brilho_fundo = models.FloatField(
        default=0.45, help_text='0.1 = bem escuro · 1.0 = imagem original.')

    # ── Topo ─────────────────────────────────────────────────────────────────
    foto_perfil = models.ImageField(upload_to='pressel/', max_length=500,
                                    blank=True, null=True)
    mostrar_online = models.BooleanField(default=True)
    texto_online = models.CharField(max_length=60, default='Estou online agora')
    nome_exibicao = models.CharField(max_length=80, default='Seu nome')
    # O destaque rosa do modelo: o usuário marca com **asteriscos**.
    descricao = models.CharField(
        max_length=300,
        default='Hoje eu resolvi liberar meu **canal privado** por algumas horas...',
        help_text='Use **texto** para destacar em rosa.')

    # ── Botão 1 (azul no modelo) ─────────────────────────────────────────────
    btn1_ativo = models.BooleanField(default=True)
    btn1_titulo = models.CharField(max_length=80, default='MEU TELEGRAM')
    btn1_subtitulo = models.CharField(
        max_length=120, default='CLIQUE AQUI E ACESSE MEU VIP NO TELEGRAM')
    btn1_link = models.URLField(max_length=500, blank=True)
    btn1_icone = models.ImageField(upload_to='pressel/', max_length=500,
                                   blank=True, null=True)
    btn1_cor_a = models.CharField(max_length=9, default='#0088cc')
    btn1_cor_b = models.CharField(max_length=9, default='#00aaff')

    # ── Botão 2 (laranja no modelo) ──────────────────────────────────────────
    btn2_ativo = models.BooleanField(default=True)
    btn2_titulo = models.CharField(max_length=80, default='Privacy 50% Desconto 🔥')
    btn2_subtitulo = models.CharField(
        max_length=120, default='CLIQUE AQUI E VEJA NOSSO PRIVACY')
    btn2_link = models.URLField(max_length=500, blank=True)
    btn2_icone = models.ImageField(upload_to='pressel/', max_length=500,
                                   blank=True, null=True)
    btn2_cor_a = models.CharField(max_length=9, default='#ff6b35')
    btn2_cor_b = models.CharField(max_length=9, default='#ff8c42')

    # ── Grade de conteúdos ───────────────────────────────────────────────────
    titulo_cards = models.CharField(max_length=80, default='Conteúdos privados')
    desfoque_cards = models.IntegerField(
        default=6, help_text='Desfoque das miniaturas, em pixels.')
    desfoque_cards_hover = models.IntegerField(
        default=3, help_text='Desfoque ao passar o mouse (efeito de "espiar").')

    card1_imagem = models.ImageField(upload_to='pressel/', max_length=500, blank=True, null=True)
    card1_texto = models.CharField(max_length=80, default='Vídeos privados de hoje 🔒', blank=True)
    card2_imagem = models.ImageField(upload_to='pressel/', max_length=500, blank=True, null=True)
    card2_texto = models.CharField(max_length=80, default='Fotos exclusivas 🔒', blank=True)
    card3_imagem = models.ImageField(upload_to='pressel/', max_length=500, blank=True, null=True)
    card3_texto = models.CharField(max_length=80, default='', blank=True)
    card4_imagem = models.ImageField(upload_to='pressel/', max_length=500, blank=True, null=True)
    card4_texto = models.CharField(max_length=80, default='', blank=True)

    # ── Rodapé ───────────────────────────────────────────────────────────────
    # O aviso de 18+ vem preenchido: a página do modelo tinha, e é o mínimo
    # esperado por quem hospeda (Netlify/Vercel) e pelas plataformas de destino.
    rodape = models.CharField(
        max_length=200, default='Conteúdo exclusivo para maiores de 18 anos.', blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-atualizado_em']

    def __str__(self):
        return self.nome

    # ── Helpers de renderização ──────────────────────────────────────────────
    @property
    def cards(self):
        """Os cards preenchidos, na ordem. Card sem imagem E sem texto some."""
        saida = []
        for i in (1, 2, 3, 4):
            img = getattr(self, f'card{i}_imagem')
            txt = getattr(self, f'card{i}_texto')
            if img or txt:
                saida.append({'imagem': img, 'texto': txt})
        return saida

    @property
    def imagens(self):
        """Todos os campos de imagem, para o exportador embutir."""
        return {
            'fundo': self.imagem_fundo or self.foto_perfil,
            'perfil': self.foto_perfil,
            'btn1': self.btn1_icone,
            'btn2': self.btn2_icone,
            'card1': self.card1_imagem,
            'card2': self.card2_imagem,
            'card3': self.card3_imagem,
            'card4': self.card4_imagem,
        }

    def descricao_html(self):
        """Converte **destaque** no <span class="highlight"> do modelo.

        Escapa o resto: o texto vai parar num arquivo HTML que o usuário publica,
        então não pode virar porta de entrada para script.
        """
        import re
        from django.utils.html import escape
        from django.utils.safestring import mark_safe

        partes = re.split(r'\*\*(.+?)\*\*', self.descricao or '')
        html = ''
        for i, p in enumerate(partes):
            html += (f'<span class="highlight">{escape(p)}</span>'
                     if i % 2 else escape(p))
        return mark_safe(html)
