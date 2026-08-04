# -*- coding: utf-8 -*-
"""Quais abas do painel cada usuário enxerga.

O admin pode esconder abas por usuário (ex.: um cliente que não contratou o
Downloader). O registro é ÚNICO e mora aqui porque três lugares precisam dele e
não podem divergir:

  1. o menu lateral, que decide o que mostrar;
  2. o middleware, que BLOQUEIA o acesso direto pela URL;
  3. a tela de gestão, que lista as abas para o admin marcar.

Esconder sem bloquear seria teatro: o link some do menu, mas quem digita
/library/downloader/ entra assim mesmo. Por isso cada aba declara as rotas que
pertencem a ela, e o middleware usa a MESMA lista.
"""

# chave -> (rótulo, grupo no menu, [(namespace, url_name), ...])
# O par (namespace, url_name) é o mesmo que o menu usa para marcar o item ativo.
ABAS = {
    'dashboard':  ('Dashboard', 'Geral', [('analytics', 'dashboard')]),

    'composer':   ('Postar', 'Publicação', [('publisher', 'composer'),
                                            ('publisher', 'campanha_ok')]),
    'fila':       ('Fila de Posts', 'Publicação', [('publisher', 'queue'),
                                                   ('publisher', 'remove'),
                                                   ('publisher', 'bulk')]),
    'publicados': ('Publicados', 'Publicação', [('publisher', 'historico')]),
    'loops':      ('Loop', 'Publicação', [('publisher', 'loops')]),
    'stories':    ('Stories', 'Publicação', [('publisher', 'stories'),
                                             ('instagram', 'stories_ativos')]),
    'agenda':     ('Agendamentos', 'Publicação', [('publisher', 'schedule'),
                                                  ('publisher', 'criar_agenda'),
                                                  ('publisher', 'editar_agenda')]),

    'biblioteca': ('Biblioteca', 'Conteúdo', [('library', 'media'),
                                              ('library', 'upload_media'),
                                              ('library', 'add_folder')]),
    'legendas':   ('Legendas', 'Conteúdo', [('library', 'captions'),
                                            ('library', 'add_caption'),
                                            ('library', 'edit_caption')]),
    'audios':     ('Áudios', 'Conteúdo', [('library', 'audios'),
                                          ('library', 'add_audio')]),
    'downloader': ('Downloader', 'Conteúdo', [('library', 'downloader'),
                                              ('library', 'start_download'),
                                              ('library', 'downloads_status')]),
    'cta':        ('Gerador de CTA', 'Conteúdo', [('library', 'cta'),
                                                  ('library', 'cta_previa')]),
    'pressel':    ('Pressels', 'Conteúdo', [('pressel', None)]),

    'performance': ('Performance', 'Análise', [('analytics', 'performance')]),
    'top_posts':   ('Top Posts', 'Análise', [('analytics', 'top_posts')]),

    'gestao_contas': ('Gestão de contas', 'Contas', [('instagram', 'gestao'),
                                                     ('instagram', 'gestao_massa')]),
    'contas':        ('Contas', 'Contas', [('instagram', 'list')]),
    'saude':         ('Saúde', 'Contas', [('analytics', 'health')]),
    'proxies':       ('Proxies', 'Contas', [('instagram', 'proxies')]),

    'aquecimento': ('Aquecimento', 'Ferramentas', [('instagram', 'warmup')]),
    'bulk_edit':   ('Edição em massa', 'Ferramentas', [('instagram', 'bulk_edit')]),

    'alertas': ('Alertas', 'Conta', [('notifications', 'alertas')]),
}

# Abas que NUNCA podem ser escondidas: sem elas o usuário fica sem saída
# (não consegue nem trocar a senha ou sair).
NUNCA_OCULTAR = {'settings'}


def chaves_validas():
    return set(ABAS)


def por_grupo():
    """[(grupo, [(chave, rótulo), ...]), ...] na ordem do menu."""
    grupos, ordem = {}, []
    for chave, (rotulo, grupo, _rotas) in ABAS.items():
        if grupo not in grupos:
            grupos[grupo] = []
            ordem.append(grupo)
        grupos[grupo].append((chave, rotulo))
    return [(g, grupos[g]) for g in ordem]


def limpar(chaves):
    """Filtra o que veio do formulário: só chaves que existem de verdade."""
    validas = chaves_validas()
    return sorted({c.strip() for c in chaves if c.strip() in validas}
                  - NUNCA_OCULTAR)


def aba_da_rota(namespace, url_name):
    """A qual aba pertence esta rota (ou None se não pertence a nenhuma).

    O par (namespace, None) casa com o namespace inteiro — usado pelo app de
    pressel, que tem várias rotas e todas fazem parte da mesma aba.
    """
    for chave, (_rotulo, _grupo, rotas) in ABAS.items():
        for ns, nome in rotas:
            if ns != namespace:
                continue
            if nome is None or nome == url_name:
                return chave
    return None
