from django import forms

from .models import Pressel

# Classes do tema escuro do painel, aplicadas em bloco para não repetir style
# inline em 30 campos.
_TXT = {'class': 'form-control form-control-sm text-white border-subtle',
        'style': 'background: var(--bg-input);'}
_COR = {'class': 'form-control form-control-color', 'style': 'height:34px;'}
_ARQ = {'class': 'form-control form-control-sm text-white border-subtle',
        'style': 'background: var(--bg-input);', 'accept': 'image/*'}


class PresselForm(forms.ModelForm):
    class Meta:
        model = Pressel
        exclude = ['owner', 'criado_em', 'atualizado_em']
        widgets = {
            'nome': forms.TextInput(attrs=_TXT),
            'titulo_pagina': forms.TextInput(attrs=_TXT),
            'imagem_fundo': forms.ClearableFileInput(attrs=_ARQ),
            'desfoque_fundo': forms.NumberInput(attrs={**_TXT, 'min': 0, 'max': 40}),
            'brilho_fundo': forms.NumberInput(attrs={**_TXT, 'min': 0.1, 'max': 1, 'step': 0.05}),
            'foto_perfil': forms.ClearableFileInput(attrs=_ARQ),
            'texto_online': forms.TextInput(attrs=_TXT),
            'nome_exibicao': forms.TextInput(attrs=_TXT),
            'descricao': forms.TextInput(attrs=_TXT),
            'btn1_titulo': forms.TextInput(attrs=_TXT),
            'btn1_subtitulo': forms.TextInput(attrs=_TXT),
            'btn1_link': forms.URLInput(attrs={**_TXT, 'placeholder': 'https://t.me/seubot'}),
            'btn1_icone': forms.ClearableFileInput(attrs=_ARQ),
            'btn1_cor_a': forms.TextInput(attrs={**_COR, 'type': 'color'}),
            'btn1_cor_b': forms.TextInput(attrs={**_COR, 'type': 'color'}),
            'btn2_titulo': forms.TextInput(attrs=_TXT),
            'btn2_subtitulo': forms.TextInput(attrs=_TXT),
            'btn2_link': forms.URLInput(attrs={**_TXT, 'placeholder': 'https://...'}),
            'btn2_icone': forms.ClearableFileInput(attrs=_ARQ),
            'btn2_cor_a': forms.TextInput(attrs={**_COR, 'type': 'color'}),
            'btn2_cor_b': forms.TextInput(attrs={**_COR, 'type': 'color'}),
            'titulo_cards': forms.TextInput(attrs=_TXT),
            'desfoque_cards': forms.NumberInput(attrs={**_TXT, 'min': 0, 'max': 30}),
            'desfoque_cards_hover': forms.NumberInput(attrs={**_TXT, 'min': 0, 'max': 30}),
            'card1_imagem': forms.ClearableFileInput(attrs=_ARQ),
            'card1_texto': forms.TextInput(attrs=_TXT),
            'card2_imagem': forms.ClearableFileInput(attrs=_ARQ),
            'card2_texto': forms.TextInput(attrs=_TXT),
            'card3_imagem': forms.ClearableFileInput(attrs=_ARQ),
            'card3_texto': forms.TextInput(attrs=_TXT),
            'card4_imagem': forms.ClearableFileInput(attrs=_ARQ),
            'card4_texto': forms.TextInput(attrs=_TXT),
            'rodape': forms.TextInput(attrs=_TXT),
        }

    def clean_desfoque_fundo(self):
        return max(0, min(int(self.cleaned_data['desfoque_fundo'] or 0), 40))

    def clean_brilho_fundo(self):
        return max(0.05, min(float(self.cleaned_data['brilho_fundo'] or 1), 1.0))

    def clean_desfoque_cards(self):
        return max(0, min(int(self.cleaned_data['desfoque_cards'] or 0), 30))

    def clean_desfoque_cards_hover(self):
        # O hover é o "espiadinha": tem que ser menor ou igual ao desfoque base,
        # senão o efeito inverte (fica MAIS borrado ao passar o mouse).
        base = self.cleaned_data.get('desfoque_cards', 6)
        v = max(0, min(int(self.cleaned_data['desfoque_cards_hover'] or 0), 30))
        return min(v, base)
