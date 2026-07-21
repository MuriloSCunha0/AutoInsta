from instagrapi import Client as InstagrapiClient
from instagrapi.exceptions import (
    LoginRequired, ChallengeRequired, TwoFactorRequired, BadPassword
)
from .session_manager import SessionManager

class InstagramEngine:
    def __init__(self, account, code_getter=None):
        self.account = account
        self.code_getter = code_getter
        self.client = InstagrapiClient()
        
        # We define a custom challenge_code_handler that hooks into our code_getter
        def custom_challenge_code_handler(username, choice):
            if not self.code_getter:
                return False
            self.account.status = 'challenge_required'
            self.account.last_error = ''
            self.account.save(update_fields=['status', 'last_error'])
            
            code = self.code_getter()
            if code:
                return code
            return False
            
        self.client.challenge_code_handler = custom_challenge_code_handler

    def _attempt_login(self, username, password, relogin=False):
        try:
            self.client.login(username, password, relogin=relogin)
        except BadPassword:
            # Trick 2: Double-Tap (Bypass do erro falso de Senha Incorreta em Datacenter)
            self.client.login(username, password, relogin=relogin)

    def login(self):
        if self.account.proxy_url:
            self.client.set_proxy(self.account.proxy_url)

        # Trick 1: Spoofing de versão moderna do app para evitar bloqueio direto
        self.client.device_settings["app_version"] = "361.0.0.39.109"
        self.client.device_settings["version_code"] = "574767436"

        username = self.account.ig_username
        password = self.account.get_ig_password()

        session_loaded = SessionManager.load_session(self.account, self.client)

        try:
            if session_loaded:
                try:
                    self._attempt_login(username, password)
                    self.client.get_timeline_feed()
                except LoginRequired:
                    old = self.client.get_settings()
                    self.client.set_settings({})
                    self.client.set_uuids(old.get('uuids', {}))
                    self._attempt_login(username, password, relogin=True)
            else:
                SessionManager.ensure_device(self.account, self.client)
                self._attempt_login(username, password)

            SessionManager.save_session(self.account, self.client)
            self.account.status = 'active'
            self._fetch_profile_info()
            return True

        except ChallengeRequired:
            # If challenge_code_handler fails to return a valid code (e.g. timeout), it raises this.
            self.account.status = 'challenge_required'
            self.account.last_error = 'O tempo para inserir o código esgotou. Tente conectar novamente.'
            self.account.save()
            raise
            
        except TwoFactorRequired as e:
            self.account.status = '2fa_required'
            self.account.last_error = ''
            self.account.save(update_fields=['status', 'last_error'])
            
            if not self.code_getter:
                raise

            # Block and wait for the code from Redis
            code = self.code_getter()
            if not code:
                self.account.last_error = 'O tempo para inserir o código 2FA esgotou. Tente novamente.'
                self.account.save(update_fields=['last_error'])
                raise Exception("Timeout aguardando 2FA")
            
            # Submits the code natively using the same instagrapi client instance
            self.client.login(username, password, verification_code=code)
            SessionManager.save_session(self.account, self.client)
            self.account.status = 'active'
            self._fetch_profile_info()
            return True
            
        except BadPassword:
            # This is the datacenter IP block.
            self.account.status = 'error'
            self.account.last_error = (
                'O Instagram bloqueou a tentativa por segurança (IP de Datacenter). '
                'Para resolver instantaneamente: vá no seu aplicativo, ative a '
                'Autenticação de Dois Fatores (2FA) e tente conectar novamente aqui.'
            )
            self.account.save()
            raise
            
        except Exception as e:
            self.account.status = 'error'
            self.account.last_error = str(e)
            self.account.save()
            raise
            
    def _fetch_profile_info(self):
        user_info = self.client.user_info_by_username(self.account.ig_username)
        # Trunca campos de texto: URLs de CDN do IG passam de 200 chars e
        # estouravam a coluna ("value too long for type character varying").
        self.account.profile_pic_url = str(user_info.profile_pic_url or '')[:1000]
        self.account.followers_count = user_info.follower_count
        self.account.following_count = user_info.following_count
        self.account.posts_count = user_info.media_count
        self.account.full_name = (user_info.full_name or '')[:255]
        self.account.bio = user_info.biography
        self.account.save()

    def login_by_session(self, sessionid):
        if self.account.proxy_url:
            self.client.set_proxy(self.account.proxy_url)

        sessionid = (sessionid or '').strip()
        if not sessionid:
            raise ValueError('sessionid vazio')

        try:
            self.client.login_by_sessionid(sessionid)
            logged_username = (self.client.username or '').lstrip('@').lower()
            expected = (self.account.ig_username or '').lstrip('@').lower()

            if not expected:
                self.account.ig_username = logged_username
            elif logged_username and logged_username != expected:
                self.account.status = 'error'
                self.account.last_error = f'A sessão pertence a @{logged_username}, não a @{expected}.'
                self.account.save()
                raise ValueError('Sessão de conta diferente da informada.')

            SessionManager.save_session(self.account, self.client)
            try:
                self.account.ig_user_id = int(self.client.user_id)
            except (TypeError, ValueError):
                pass
            self.account.status = 'active'
            self._fetch_profile_info()
            return True

        except ValueError:
            raise
        except Exception as e:
            self.account.status = 'error'
            self.account.last_error = f'Não foi possível validar a sessão. Ela pode ter expirado. ({e})'
            self.account.save()
            raise

    def _prepare_client(self):
        """Garante um client logado via sessão salva (para ações fora do upload)."""
        if self.account.proxy_url:
            self.client.set_proxy(self.account.proxy_url)
        SessionManager.load_session(self.account, self.client)
        self.client.login(self.account.ig_username, self.account.get_ig_password())

    def edit_profile(self, full_name=None, biography=None, external_url=None):
        """Edita bio/nome/link do perfil (via engine cinza — funciona em contas
        Pessoais também, o que a API oficial não permite)."""
        self._prepare_client()
        data = {}
        if full_name is not None:
            data['full_name'] = full_name
        if biography is not None:
            data['biography'] = biography
        if external_url is not None:
            data['external_url'] = external_url
        result = self.client.account_edit(**data)
        try:
            self._fetch_profile_info()
        except Exception:
            pass
        return result

    def change_profile_picture(self, image_path):
        """Troca a foto de perfil da conta."""
        self._prepare_client()
        return self.client.account_change_picture(image_path)

    def run_warmup(self, likes=0, follows=0, views=0, hashtag='reels'):
        """Aquecimento gradual: curte/visualiza/segue conteúdo de um hashtag.
        Best-effort — cada ação é isolada para uma falha não abortar o lote."""
        self._prepare_client()
        done = {'likes': 0, 'follows': 0, 'views': 0}

        amount = max(likes, follows, views, 1)
        try:
            medias = self.client.hashtag_medias_recent(hashtag, amount=amount)
        except Exception:
            try:
                medias = self.client.hashtag_medias_top(hashtag, amount=amount)
            except Exception:
                medias = []

        # Visualizações (media_seen aceita lista de pks)
        if views and medias:
            try:
                self.client.media_seen([m.pk for m in medias[:views]])
                done['views'] = min(views, len(medias))
            except Exception:
                pass

        # Curtidas
        for m in medias[:likes]:
            try:
                self.client.media_like(m.id)
                done['likes'] += 1
            except Exception:
                pass

        # Follows (usuários dos primeiros posts)
        for m in medias[:follows]:
            try:
                self.client.user_follow(m.user.pk)
                done['follows'] += 1
            except Exception:
                pass

        return done

    def upload_reel(self, video_path, caption, thumbnail_path=None):
        SessionManager.load_session(self.account, self.client)
        self.client.login(self.account.ig_username, self.account.get_ig_password())
        
        media = self.client.clip_upload(
            path=video_path,
            caption=caption,
            thumbnail=thumbnail_path
        )
        return media.dict()

    def upload_reel_meta_api(self, video_url, caption, cover_url=None, share_to_feed=True):
        """
        Publica um Reel usando a API oficial da Meta.
        Exige que a conta seja Business/Creator e que tenha um meta_access_token ativo.
        O video_url e cover_url devem ser URLs públicas acessíveis pelos servidores da Meta.
        share_to_feed=True faz o Reel aparecer também na grade principal do perfil
        (parâmetro oficial `share_to_feed` da doc de Content Publishing).
        """
        import requests
        import time
        from django.conf import settings

        if not self.account.meta_access_token:
            raise ValueError("Conta não possui token Meta configurado.")

        ig_user_id = self.account.ig_user_id
        if not ig_user_id:
            raise ValueError("Conta não possui ig_user_id. Reconecte o Meta API.")

        token = self.account.get_meta_token()

        # 1. Cria o contêiner de mídia
        url = f"https://graph.instagram.com/v23.0/{ig_user_id}/media"
        payload = {
            'media_type': 'REELS',
            'video_url': video_url,
            'caption': caption,
            'share_to_feed': 'true' if share_to_feed else 'false',
            'access_token': token
        }
        if cover_url:
            payload['cover_url'] = cover_url
            
        res = requests.post(url, data=payload, timeout=20)
        data = res.json()
        
        if 'id' not in data:
            raise Exception(f"Erro ao criar contêiner Meta: {data.get('error', data)}")
            
        creation_id = data['id']
        
        # 2. Polling para ver se o vídeo terminou de processar
        status_url = f"https://graph.instagram.com/v23.0/{creation_id}"
        status_params = {
            'fields': 'status_code',
            'access_token': token
        }
        
        max_attempts = 12
        ready = False
        
        for _ in range(max_attempts):
            time.sleep(10)
            status_res = requests.get(status_url, params=status_params, timeout=10)
            status_data = status_res.json()
            
            status_code = status_data.get('status_code')
            if status_code == 'FINISHED':
                ready = True
                break
            elif status_code == 'ERROR':
                raise Exception(f"Erro no processamento do vídeo pela Meta: {status_data}")
                
        if not ready:
            raise Exception("Timeout aguardando processamento do vídeo na Meta.")
            
        # 3. Publica a mídia
        publish_url = f"https://graph.instagram.com/v23.0/{ig_user_id}/media_publish"
        publish_payload = {
            'creation_id': creation_id,
            'access_token': token
        }
        
        pub_res = requests.post(publish_url, data=publish_payload, timeout=20)
        pub_data = pub_res.json()
        
        if 'id' not in pub_data:
            raise Exception(f"Erro ao publicar mídia via Meta: {pub_data.get('error', pub_data)}")
            
        return {'id': pub_data['id'], 'creation_id': creation_id}

