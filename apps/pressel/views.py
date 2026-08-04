import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from . import exportador
from .forms import PresselForm
from .models import Pressel


@login_required
def lista(request):
    return render(request, 'pressel/lista.html', {
        'pressels': Pressel.objects.filter(owner=request.user),
    })


@login_required
def nova(request):
    """Cria já com os padrões do modelo — o usuário só troca o que quiser."""
    p = Pressel.objects.create(owner=request.user, nome='Nova pressel')
    return redirect('pressel:editar', pressel_id=p.id)


@login_required
def editar(request, pressel_id):
    p = get_object_or_404(Pressel, id=pressel_id, owner=request.user)
    if request.method == 'POST':
        form = PresselForm(request.POST, request.FILES, instance=p)
        if form.is_valid():
            form.save()
            messages.success(request, 'Pressel salva.')
            return redirect('pressel:editar', pressel_id=p.id)
        messages.error(request, 'Confira os campos destacados.')
    else:
        form = PresselForm(instance=p)
    # Os 4 cards são pares (imagem, texto) — emparelhar aqui deixa o template
    # com um `for` em vez de 8 blocos copiados.
    cards_form = [(form[f'card{i}_imagem'], form[f'card{i}_texto']) for i in (1, 2, 3, 4)]
    return render(request, 'pressel/editor.html', {
        'p': p, 'form': form, 'cards_form': cards_form,
    })


@login_required
@xframe_options_sameorigin
def previa(request, pressel_id):
    """A página real, servida para o <iframe> do editor."""
    p = get_object_or_404(Pressel, id=pressel_id, owner=request.user)
    return HttpResponse(exportador.html_para_previa(p, request))


def _nome_arquivo(nome):
    """Vira um nome de arquivo seguro: sem acento, espaço nem barra."""
    import unicodedata
    base = unicodedata.normalize('NFKD', nome or 'pressel')
    base = base.encode('ascii', 'ignore').decode('ascii')
    base = re.sub(r'[^A-Za-z0-9._-]+', '-', base).strip('-.') or 'pressel'
    return f'{base.lower()}.html'


@login_required
def baixar(request, pressel_id):
    """Baixa o HTML pronto: arquivo único, com as imagens embutidas."""
    p = get_object_or_404(Pressel, id=pressel_id, owner=request.user)
    html = exportador.html_para_download(p)
    resp = HttpResponse(html, content_type='text/html; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="{_nome_arquivo(p.nome)}"'
    return resp


@login_required
@require_POST
def duplicar(request, pressel_id):
    """Copia uma pressel — o fluxo real é 1 pressel por modelo/campanha, e
    recomeçar do zero a cada uma seria trabalho repetido."""
    orig = get_object_or_404(Pressel, id=pressel_id, owner=request.user)
    copia = Pressel.objects.get(pk=orig.pk)
    copia.pk = None
    copia.nome = f'{orig.nome} (cópia)'
    copia.save()
    messages.success(request, 'Pressel duplicada.')
    return redirect('pressel:editar', pressel_id=copia.id)


@login_required
@require_POST
def excluir(request, pressel_id):
    p = get_object_or_404(Pressel, id=pressel_id, owner=request.user)
    p.delete()
    messages.success(request, 'Pressel excluída.')
    return redirect('pressel:lista')
