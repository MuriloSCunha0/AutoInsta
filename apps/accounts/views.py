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

@login_required
def settings_view(request):
    accounts = InstagramAccount.objects.filter(owner=request.user)
    
    if request.method == 'POST' and request.POST.get('action') == 'update_meta_api':
        account_id = request.POST.get('account_id')
        meta_token = request.POST.get('meta_token')
        try:
            acc = accounts.get(id=account_id)
            acc.meta_access_token = meta_token
            acc.save()
            messages.success(request, f'Token da Meta atualizado para a conta @{acc.ig_username}.')
        except InstagramAccount.DoesNotExist:
            messages.error(request, 'Conta não encontrada.')
        return redirect('accounts:settings')

    return render(request, 'accounts/settings.html', {'accounts': accounts})
