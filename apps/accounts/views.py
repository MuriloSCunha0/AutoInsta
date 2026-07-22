from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from .forms import CustomUserCreationForm
from django.urls import reverse_lazy
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash

def get_client_ip(request):
    """IP real do cliente, respeitando o proxy (Caddy) via X-Forwarded-For."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


class CustomLoginView(LoginView):
    template_name = 'accounts/login.html'
    redirect_authenticated_user = True

    def form_valid(self, form):
        """Credenciais válidas: aplica a trava de IP antes de concluir o login."""
        user = form.get_user()
        ip = get_client_ip(self.request)

        if user.ip_locked and user.bound_ip and ip != user.bound_ip:
            messages.error(
                self.request,
                'Esta conta está travada para outro IP. Acesso permitido apenas '
                'do local autorizado. Fale com o administrador.'
            )
            return self.form_invalid(form)

        # Registra o último IP; se a trava está ligada mas sem IP fixado ainda,
        # fixa neste primeiro acesso.
        user.last_login_ip = ip
        if user.ip_locked and not user.bound_ip:
            user.bound_ip = ip
        user.save(update_fields=['last_login_ip', 'bound_ip'])
        return super().form_valid(form)

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
            request.user.nickname = (request.POST.get('nickname') or '').strip()
            
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


from .models import MetaApp
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST


@login_required
@require_POST
def add_meta_app(request):
    """Cadastra mais um app Meta (o usuário pode ter vários)."""
    name = (request.POST.get('name') or '').strip()
    if not name:
        messages.error(request, 'Dê um nome para o app (ex.: "App principal").')
        return redirect('accounts:settings')

    if MetaApp.objects.filter(owner=request.user, name=name).exists():
        messages.error(request, f'Você já tem um app chamado "{name}".')
        return redirect('accounts:settings')

    app = MetaApp(
        owner=request.user,
        name=name,
        meta_app_id=(request.POST.get('meta_app_id') or '').strip(),
        meta_login_config_id=(request.POST.get('meta_login_config_id') or '').strip(),
        instagram_app_id=(request.POST.get('instagram_app_id') or '').strip(),
    )
    app.set_meta_secret(request.POST.get('meta_app_secret'))
    app.set_instagram_secret(request.POST.get('instagram_app_secret'))
    app.save()

    # O primeiro app cadastrado já vira o ativo.
    if MetaApp.objects.filter(owner=request.user).count() == 1:
        app.activate()

    messages.success(request, f'App "{name}" cadastrado.')
    return redirect('accounts:settings')


@login_required
@require_POST
def update_meta_app(request, app_id):
    """Edita um app existente (secrets em branco mantêm o valor atual)."""
    app = get_object_or_404(MetaApp, id=app_id, owner=request.user)
    name = (request.POST.get('name') or '').strip()
    if name:
        app.name = name
    app.meta_app_id = (request.POST.get('meta_app_id') or '').strip()
    app.meta_login_config_id = (request.POST.get('meta_login_config_id') or '').strip()
    app.instagram_app_id = (request.POST.get('instagram_app_id') or '').strip()

    if (request.POST.get('meta_app_secret') or '').strip():
        app.set_meta_secret(request.POST.get('meta_app_secret'))
    if (request.POST.get('instagram_app_secret') or '').strip():
        app.set_instagram_secret(request.POST.get('instagram_app_secret'))

    app.save()
    messages.success(request, f'App "{app.name}" atualizado.')
    return redirect('accounts:settings')


@login_required
def activate_meta_app(request, app_id):
    app = get_object_or_404(MetaApp, id=app_id, owner=request.user)
    app.activate()
    messages.success(request, f'"{app.name}" agora é o app usado nas novas conexões.')
    return redirect('accounts:settings')


@login_required
def delete_meta_app(request, app_id):
    app = get_object_or_404(MetaApp, id=app_id, owner=request.user)
    was_active = app.is_active
    nome = app.name
    app.delete()
    # Se removeu o ativo, promove outro para não ficar sem app.
    if was_active:
        proximo = MetaApp.objects.filter(owner=request.user).first()
        if proximo:
            proximo.activate()
    messages.success(request, f'App "{nome}" removido.')
    return redirect('accounts:settings')


@login_required
def update_meta_credentials(request):
    """Salva as credenciais dos apps Meta/Instagram do próprio usuário."""
    if request.method == 'POST':
        u = request.user
        u.meta_app_id = (request.POST.get('meta_app_id') or '').strip()
        u.meta_login_config_id = (request.POST.get('meta_login_config_id') or '').strip()
        u.instagram_app_id = (request.POST.get('instagram_app_id') or '').strip()

        # Secrets em branco = mantêm o valor atual (não sobrescrevem).
        meta_secret = (request.POST.get('meta_app_secret') or '').strip()
        if meta_secret:
            u.set_meta_app_secret(meta_secret)
        elif not u.meta_app_id:
            u.set_meta_app_secret('')

        ig_secret = (request.POST.get('instagram_app_secret') or '').strip()
        if ig_secret:
            u.set_instagram_app_secret(ig_secret)
        elif not u.instagram_app_id:
            u.set_instagram_app_secret('')

        u.save(update_fields=[
            'meta_app_id', 'meta_app_secret_enc', 'meta_login_config_id',
            'instagram_app_id', 'instagram_app_secret_enc',
        ])
        messages.success(request, 'Credenciais salvas com sucesso.')

    return redirect('accounts:settings')


@login_required
def settings_view(request):
    accounts = InstagramAccount.objects.filter(owner=request.user)

    # URL de callback (global) que o usuário precisa registrar no app Meta dele.
    redirect_uri = getattr(django_settings, 'META_REDIRECT_URI', '')

    meta_apps = MetaApp.objects.filter(owner=request.user)
    active_app = request.user.get_active_meta_app()

    return render(request, 'accounts/settings.html', {
        'accounts': accounts,
        'meta_apps': meta_apps,
        'active_app': active_app,
        'meta_ready': bool(active_app and active_app.is_complete),
        'meta_redirect_uri': redirect_uri,
    })
