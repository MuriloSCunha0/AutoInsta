from django import forms
from .models import ScheduledPost

class ScheduledPostForm(forms.ModelForm):
    class Meta:
        model = ScheduledPost
        fields = ['account', 'video_file', 'caption', 'scheduled_for']
        widgets = {
            'account': forms.Select(attrs={'class': 'form-select text-white border-subtle', 'style': 'background: var(--bg-input)'}),
            'video_file': forms.FileInput(attrs={'class': 'form-control text-white border-subtle', 'style': 'background: var(--bg-input)'}),
            'caption': forms.Textarea(attrs={'class': 'form-control text-white border-subtle', 'style': 'background: var(--bg-input)', 'rows': 4}),
            'scheduled_for': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control text-white border-subtle', 'style': 'background: var(--bg-input)'}),
        }
