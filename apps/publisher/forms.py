from django import forms
from .models import ScheduledPost, PostLoop

class ScheduledPostForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Evita o "---------" padrão do Django no select de conta.
        self.fields['account'].empty_label = 'Selecione uma conta…'

    class Meta:
        model = ScheduledPost
        fields = ['account', 'post_type', 'video_file', 'caption', 'scheduled_for']
        widgets = {
            'account': forms.Select(attrs={'class': 'form-select text-white border-subtle', 'style': 'background: var(--bg-input)'}),
            'post_type': forms.Select(attrs={'class': 'form-select text-white border-subtle', 'style': 'background: var(--bg-input)'}),
            'video_file': forms.FileInput(attrs={'class': 'form-control text-white border-subtle', 'style': 'background: var(--bg-input)'}),
            'caption': forms.Textarea(attrs={'class': 'form-control text-white border-subtle', 'style': 'background: var(--bg-input)', 'rows': 4}),
            'scheduled_for': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control text-white border-subtle', 'style': 'background: var(--bg-input)'}),
        }

class PostLoopForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['account'].empty_label = 'Selecione uma conta…'

    class Meta:
        model = PostLoop
        fields = ['account', 'post_type', 'video_file', 'caption', 'interval_days']
        widgets = {
            'account': forms.Select(attrs={'class': 'form-select text-white border-subtle', 'style': 'background: var(--bg-input)'}),
            'post_type': forms.Select(attrs={'class': 'form-select text-white border-subtle', 'style': 'background: var(--bg-input)'}),
            'video_file': forms.FileInput(attrs={'class': 'form-control text-white border-subtle', 'style': 'background: var(--bg-input)'}),
            'caption': forms.Textarea(attrs={'class': 'form-control text-white border-subtle', 'style': 'background: var(--bg-input)', 'rows': 4}),
            'interval_days': forms.NumberInput(attrs={'class': 'form-control text-white border-subtle', 'style': 'background: var(--bg-input)'}),
        }
