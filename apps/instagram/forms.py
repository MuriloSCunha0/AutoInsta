from django import forms
from .models import InstagramAccount

class AddInstagramAccountForm(forms.ModelForm):
    class Meta:
        model = InstagramAccount
        fields = ['ig_username', 'proxy_url']
        widgets = {
            'ig_username': forms.TextInput(attrs={'class': 'form-control text-white border-subtle', 'style': 'background: var(--bg-input)', 'placeholder': '@username'}),
            'proxy_url': forms.TextInput(attrs={'class': 'form-control text-white border-subtle', 'style': 'background: var(--bg-input)', 'placeholder': 'http://user:pass@host:port'}),
        }
    
    ig_password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-control text-white border-subtle', 'style': 'background: var(--bg-input)'}))
