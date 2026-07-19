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

    def login(self):
        if self.account.proxy_url:
            self.client.set_proxy(self.account.proxy_url)

        username = self.account.ig_username
        password = self.account.get_ig_password()

        session_loaded = SessionManager.load_session(self.account, self.client)

        try:
            if session_loaded:
                try:
                    self.client.login(username, password)
                    self.client.get_timeline_feed()
                except LoginRequired:
                    old = self.client.get_settings()
                    self.client.set_settings({})
                    self.client.set_uuids(old.get('uuids', {}))
                    self.client.login(username, password, relogin=True)
            else:
                SessionManager.ensure_device(self.account, self.client)
                self.client.login(username, password)

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
        self.account.profile_pic_url = user_info.profile_pic_url
        self.account.followers_count = user_info.follower_count
        self.account.following_count = user_info.following_count
        self.account.posts_count = user_info.media_count
        self.account.full_name = user_info.full_name
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

    def upload_reel(self, video_path, caption, thumbnail_path=None):
        SessionManager.load_session(self.account, self.client)
        self.client.login(self.account.ig_username, self.account.get_ig_password())
        
        media = self.client.clip_upload(
            path=video_path,
            caption=caption,
            thumbnail=thumbnail_path
        )
        return media.dict()
