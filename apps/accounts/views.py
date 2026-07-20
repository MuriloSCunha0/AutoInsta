from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from .forms import CustomUserCreationForm
from django.urls import reverse_lazy
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash

class CustomLoginView(LoginView):
    template_name = 'accounts/login.html'
    redirect_authenticated_user = True

def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
        
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = False  # Requer aprovação do admin
            user.save()
            return render(request, 'accounts/register_success.html', {'user': user})
    else:
        form = CustomUserCreationForm()
    
    return render(request, 'accounts/register.html', {'form': form})

@login_required
def profile(request):
    return render(request, 'accounts/profile.html')

@login_required
def profile_update(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'update_details':
            request.user.first_name = request.POST.get('first_name', '')
            request.user.last_name = request.POST.get('last_name', '')
            request.user.email = request.POST.get('email', '')
            request.user.phone = request.POST.get('phone', '')
            
            # Handle avatar upload if provided
            if 'avatar' in request.FILES:
                request.user.avatar = request.FILES['avatar']
                
            request.user.save()
            messages.success(request, 'Seus dados foram atualizados com sucesso.')
            
        elif action == 'update_password':
            old_password = request.POST.get('old_password')
            new_password = request.POST.get('new_password')
            confirm_password = request.POST.get('confirm_password')
            
            if not request.user.check_password(old_password):
                messages.error(request, 'A senha atual está incorreta.')
            elif new_password != confirm_password:
                messages.error(request, 'As novas senhas não coincidem.')
            else:
                request.user.set_password(new_password)
                request.user.save()
                update_session_auth_hash(request, request.user)
                messages.success(request, 'Sua senha foi alterada com sucesso.')
                
    return redirect('accounts:profile')

from apps.instagram.models import InstagramAccount
from django.conf import settings as django_settings


@login_required
def update_meta_credentials(request):
    """Salva o App ID / App Secret do app Meta do próprio usuário."""
    if request.method == 'POST':
        app_id = (request.POST.get('meta_app_id') or '').strip()
        app_secret = (request.POST.get('meta_app_secret') or '').strip()

        request.user.meta_app_id = app_id
        # Campo de senha em branco = mantém o secret atual (não sobrescreve).
        if app_secret:
            request.user.set_meta_app_secret(app_secret)
        elif not app_id:
            # Limpou tudo: zera também o secret.
            request.user.set_meta_app_secret('')

        request.user.save(update_fields=['meta_app_id', 'meta_app_secret_enc'])
        messages.success(request, 'Credenciais do app Meta salvas com sucesso.')

    return redirect('accounts:settings')


@login_required
def settings_view(request):
    accounts = InstagramAccount.objects.filter(owner=request.user)

    # URL de callback (global) que o usuário precisa registrar no app Meta dele.
    redirect_uri = getattr(django_settings, 'META_REDIRECT_URI', '')

    return render(request, 'accounts/settings.html', {
        'accounts': accounts,
        'meta_app_id': request.user.meta_app_id,
        'meta_secret_set': bool(request.user.meta_app_secret_enc),
        'meta_ready': request.user.has_meta_credentials,
        'meta_redirect_uri': redirect_uri,
    })
