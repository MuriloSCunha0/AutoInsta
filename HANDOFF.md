# Documento de Handoff: Projeto "SandraoFlow" (AutoInsta)

Este documento foi criado para registrar todo o contexto, arquitetura e diretrizes visuais do projeto, servindo como ponto de partida para qualquer desenvolvedor ou IA que for dar continuidade ao trabalho.

## 1. Identidade e Padrão Visual

O projeto passou por um redesign completo (Neon/Cyberpunk) para ter uma estética "premium SaaS" idêntica ao site modelo ([Instaflow Automation](https://instaflow-automation.lovable.app/)).

- **Tema:** Dark mode com glows (Cyberpunk/Neon).
- **Cores Principais:** 
  - Fundo principal: `#080b14` (Azul/Roxo super escuro)
  - Superfícies (Cards/Modais): Glassmorphism `rgba(12, 16, 33, 0.7)` com blur
  - Acentos (Neon Glow): Purple (`#8b5cf6`), Cyan (`#22d3ee`)
- **UI Components:**
  - **Botões:** Gradientes vibrantes `linear-gradient(135deg, #7c3aed, #06b6d4)` com `box-shadow` emitindo brilho.
  - **Sidebar:** Fundo transparente com navegação ativa destacada por um glow lateral e texto neon. Ao clicar nas opções, o sistema utiliza o `url_name` da rota (no Django) para definir o estado ativo, impedindo bugs de múltiplas abas selecionadas.
  - **Inputs:** Fundo semi-transparente `rgba(17, 23, 48, 0.8)` com bordas roxas leves, brilhando mais intensamente no `:focus`.
  - **CSS:** As customizações globais de layout e neon estão concentradas no arquivo `static/css/theme.css`.

## 2. Arquitetura do Backend

O sistema é construído em **Django** e **Celery**, com o banco de dados rodando em contêineres Docker via `docker-compose.prod.yml`.

### Apps Principais:
- **`accounts`**: Gerenciamento de usuários, login, registro e a tela de configurações (`settings`).
- **`instagram`**: Gerenciamento das contas conectadas. Permite 3 formas de login: 
  - Login web via servidor (Selenium)
  - Cookie de sessão (Fallback)
  - **Login Oficial Meta (OAuth)**: Botão "Conectar via Meta" gera o token `meta_access_token`
- **`publisher`**: Engine de postagem, fila, agendamento de posts e Stories. Usa Celery para postagem assíncrona.
- **`analytics`**: Dashboard com Top Posts (puxando via Meta API) e checagem de Rate Limit.

### Engine de Postagem (Híbrida)
O arquivo `engine/client.py` gerencia as publicações. Na tarefa `publish_reel` (em `apps/publisher/tasks.py`), há um sistema de **Roteamento Inteligente**:
1. **API Oficial (Prioridade):** Se a `InstagramAccount` possui o `meta_access_token`, a tarefa monta a URL pública do vídeo e envia os dados diretamente para os endpoints da Meta Graph API (`/media` e `/media_publish`).
2. **Automação Cinza (Fallback):** Se o token não existir (ex: contas Pessoais), o sistema faz login com a biblioteca local e publica emulando o aplicativo.

### Spintax (Variáveis nas Legendas)
O sistema aceita variáveis no texto das legendas (ex: `{nome_conta}`, `{dia_semana}`, `{data_hoje}`). Na hora do processamento em `publish_reel`, o Django substitui essas tags pelos valores reais antes de enviar à API ou Selenium.

## 3. Deployment e VPS

### Acesso à Máquina
- Os deploys estão rodando a partir da cópia local no computador do usuário, porém toda vez que for necessário atualizar a versão de produção, usamos o script de deploy.
- **Diretório Local do Projeto:** `c:\Users\Suporte\OneDrive\Área de Trabalho\Outsider\AutoInsta`
- **Comando de Deploy:** 
  O usuário criou um script para automatizar a build das imagens Docker. Para subir uma nova versão, execute no terminal:
  ```bash
  python deploy_migrate.py
  ```
  Este script faz o build do `Dockerfile` de produção, roda as migrações do banco e recria o contêiner `web` sem derrubar os serviços paralelos.

### Variáveis de Ambiente Críticas (`.env`)
- **`META_APP_ID`**, **`META_APP_SECRET`**, **`META_REDIRECT_URI`**: Necessários para que o fluxo de OAuth (Botão conectar com a Meta) funcione.
- **`SITE_URL`**: Essencial para que a publicação da Meta API funcione. A Meta precisa de uma URL pública para baixar o arquivo local de mídia durante a criação do post.
- **`FERNET_KEY`**: Chave obrigatória de segurança que encripta as senhas das contas de Instagram no banco.
- **`OPENAI_API_KEY`**: Usado pelas tasks em background (em desenvolvimento) para gerar conteúdo, se necessário.

## 4. O Que Ficou Pendente / Próximos Passos
1. **Testes do Novo Botão Meta:** O usuário precisa cadastrar o aplicativo no Facebook Developers, inserir os dados no `.env` e testar a geração do Token na aba de Contas.
2. **Exposição do Site (`SITE_URL`):** Para que a Meta faça o download do vídeo gerado localmente, a plataforma precisa estar em um domínio/IP acessível externamente (Cloudflare Tunnel, Ngrok ou IP estático configurado no `.env`).
3. **Métricas de Performance:** O menu "Performance" do analytics ainda precisa ser integrado 100% à Meta Graph API para puxar impressões reais do Instagram em vez de dados mockados do banco de dados.
