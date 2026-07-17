from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import InstagramAccount
from .forms import AddInstagramAccountForm
from .tasks import login_instagram_account, submit_challenge_code

@login_required
def account_list(request):
    accounts = InstagramAccount.objects.filter(owner=request.user)
    form = AddInstagramAccountForm()
    return render(request, 'instagram/list.html', {'accounts': accounts, 'form': form})

@login_required
def add_account(request):
    if request.method == 'POST':
        form = AddInstagramAccountForm(request.POST)
        if form.is_valid():
            account = form.save(commit=False)
            account.owner = request.user
            account.set_ig_password(form.cleaned_data['ig_password'])
            account.save()
            
            # Disparar task celery para login
            login_instagram_account.delay(account.id)
            
            # Retornar partial htmx
            return render(request, 'instagram/partials/account_card.html', {'account': account})
    return render(request, 'instagram/partials/add_modal.html', {'form': AddInstagramAccountForm()})

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
