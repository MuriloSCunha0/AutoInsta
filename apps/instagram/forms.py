from django import forms
from .models import InstagramAccount

class AddInstagramAccountForm(forms.ModelForm):
    class Meta:
        model = InstagramAccount
        fields = ['ig_username', 'proxy_url']
    
    ig_password = forms.CharField(widget=forms.PasswordInput)
