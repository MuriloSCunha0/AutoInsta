from instagrapi import Client as InstagrapiClient
from instagrapi.exceptions import (
    LoginRequired, ChallengeRequired, TwoFactorRequired, BadPassword
)
from .session_manager import SessionManager
from .challenge_handler import ChallengeHandler

class InstagramEngine:
    def __init__(self, account):
        self.account = account
        self.client = InstagrapiClient()
        self.client.challenge_code_handler = ChallengeHandler.challenge_code_handler

    def login(self):
        # Set proxy se disponivel
        if self.account.proxy_url:
            self.client.set_proxy(self.account.proxy_url)

        # Load existing session if present
        session_loaded = SessionManager.load_session(self.account, self.client)

        try:
            if session_loaded:
                # Try to login with session
                try:
                    self.client.login(self.account.ig_username, self.account.get_ig_password())
                except LoginRequired:
                    # Session expired, re-login
                    self.client.login(self.account.ig_username, self.account.get_ig_password())
            else:
                # Fresh login
                self.client.login(self.account.ig_username, self.account.get_ig_password())
            
            # Save successful session
            SessionManager.save_session(self.account, self.client)
            self.account.status = 'active'
            
            # Fetch basic info
            user_info = self.client.user_info_by_username(self.account.ig_username)
            self.account.profile_pic_url = user_info.profile_pic_url
            self.account.followers_count = user_info.follower_count
            self.account.following_count = user_info.following_count
            self.account.posts_count = user_info.media_count
            self.account.full_name = user_info.full_name
            self.account.bio = user_info.biography
            
            self.account.save()
            return True

        except ChallengeRequired:
            self.account.status = 'challenge_required'
            self.account.save()
            raise
            
        except TwoFactorRequired:
            self.account.status = '2fa_required'
            self.account.save()
            raise
            
        except BadPassword:
            self.account.status = 'error'
            self.account.last_error = 'Senha incorreta'
            self.account.save()
            raise
            
        except Exception as e:
            self.account.status = 'error'
            self.account.last_error = str(e)
            self.account.save()
            raise
            
    def login_by_session(self, sessionid):
        """Conecta a conta usando um cookie `sessionid` capturado do
        instagram.com (login real feito no navegador do usuário).

        Muito mais robusto que login usuário/senha: o Instagram não recusa
        a sessão como faz com a API privada a partir de IPs de datacenter,
        e desafios/2FA já foram resolvidos no navegador do usuário.
        """
        if self.account.proxy_url:
            self.client.set_proxy(self.account.proxy_url)

        sessionid = (sessionid or '').strip()
        if not sessionid:
            raise ValueError('sessionid vazio')

        try:
            # login_by_sessionid valida a sessão e popula user_id/username
            self.client.login_by_sessionid(sessionid)

            logged_username = (self.client.username or '').lstrip('@').lower()
            expected = (self.account.ig_username or '').lstrip('@').lower()

            # Se o usuário não informou o @, adotamos o da sessão.
            if not expected:
                self.account.ig_username = logged_username
            elif logged_username and logged_username != expected:
                self.account.status = 'error'
                self.account.last_error = (
                    f'A sessão pertence a @{logged_username}, não a @{expected}.'
                )
                self.account.save()
                raise ValueError('Sessão de conta diferente da informada.')

            SessionManager.save_session(self.account, self.client)
            try:
                self.account.ig_user_id = int(self.client.user_id)
            except (TypeError, ValueError):
                pass
            self.account.status = 'active'

            user_info = self.client.user_info_by_username(self.account.ig_username)
            self.account.profile_pic_url = user_info.profile_pic_url
            self.account.followers_count = user_info.follower_count
            self.account.following_count = user_info.following_count
            self.account.posts_count = user_info.media_count
            self.account.full_name = user_info.full_name
            self.account.bio = user_info.biography

            self.account.save()
            return True

        except ValueError:
            raise
        except Exception as e:
            self.account.status = 'error'
            self.account.last_error = (
                'Não foi possível validar a sessão. Ela pode ter expirado — '
                f'gere um novo sessionid. ({e})'
            )
            self.account.save()
            raise

    def resolve_challenge(self, code):
        self.client.challenge_resolve(code)
        SessionManager.save_session(self.account, self.client)
        self.account.status = 'active'
        self.account.save()
        
    def resolve_2fa(self, code):
        self.client.two_factor_login(code)
        SessionManager.save_session(self.account, self.client)
        self.account.status = 'active'
        self.account.save()
        
    def upload_reel(self, video_path, caption, thumbnail_path=None):
        SessionManager.load_session(self.account, self.client)
        self.client.login(self.account.ig_username, self.account.get_ig_password())
        
        media = self.client.clip_upload(
            path=video_path,
            caption=caption,
            thumbnail=thumbnail_path
        )
        return media.dict()
