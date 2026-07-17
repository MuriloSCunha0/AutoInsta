from django import forms
from .models import ScheduledPost

class ScheduledPostForm(forms.ModelForm):
    class Meta:
        model = ScheduledPost
        fields = ['account', 'video_file', 'caption', 'scheduled_for']
        widgets = {
            'scheduled_for': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }
