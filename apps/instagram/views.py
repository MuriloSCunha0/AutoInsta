from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from .models import InstagramAccount
from .forms import AddInstagramAccountForm
from .tasks import login_instagram_account, submit_challenge_code


def _toast(message, toast_type='info'):
    """Resposta HTMX vazia que só dispara um toast (lido pelo app.js)."""
    resp = HttpResponse(status=204)
    resp['HX-Reswap'] = 'none'
    resp['X-Toast-Message'] = message
    resp['X-Toast-Type'] = toast_type
    return resp

@login_required
def account_list(request):
    accounts = InstagramAccount.objects.filter(owner=request.user)
    form = AddInstagramAccountForm()
    return render(request, 'instagram/list.html', {'accounts': accounts, 'form': form})

@login_required
def add_account(request):
    if request.method != 'POST':
        return redirect('instagram:list')

    form = AddInstagramAccountForm(request.POST)
    if not form.is_valid():
        return _toast('Preencha usuário e senha corretamente.', 'error')

    username = form.cleaned_data['ig_username'].lstrip('@').strip()

    # unique_together (owner + ig_username) não é validado no form porque
    # 'owner' não faz parte dele — checamos aqui para não estourar IntegrityError.
    if InstagramAccount.objects.filter(owner=request.user, ig_username=username).exists():
        return _toast('Você já adicionou essa conta.', 'warning')

    account = form.save(commit=False)
    account.owner = request.user
    account.ig_username = username
    account.set_ig_password(form.cleaned_data['ig_password'])
    account.save()

    # Disparar task celery para login em background
    login_instagram_account.delay(account.id)

    # Retornar o card da conta (HTMX injeta na lista)
    return render(request, 'instagram/partials/account_card.html', {'account': account})

@login_required
def account_status_partial(request, account_id):
    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    return render(request, 'instagram/partials/account_card.html', {'account': account})

@login_required
def submit_challenge(request, account_id):
    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    if request.method == 'POST':
        code = request.POST.get('code')
        if code:
            account.status = 'connecting'
            account.save()
            submit_challenge_code.delay(account.id, code)
    return render(request, 'instagram/partials/account_card.html', {'account': account})
    
@login_required
def remove_account(request, account_id):
    account = get_object_or_404(InstagramAccount, id=account_id, owner=request.user)
    account.delete()
    return redirect('instagram:list')
