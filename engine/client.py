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

    IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.webp')

    def publish_meta_api(self, media_url, caption='', post_type='REELS',
                         cover_url=None, share_to_feed=True, is_image=False):
        """
        Publica via API oficial da Meta. Suporta REELS, FEED (imagem/vídeo) e STORY.

        Fluxo oficial (Content Publishing):
          POST /{ig-user-id}/media  ->  polling do status  ->  POST /{ig-user-id}/media_publish

        Regras da doc aplicadas:
          - Reels:  media_type=REELS + video_url (+ share_to_feed, cover_url)
          - Story:  media_type=STORIES + video_url|image_url (Stories NÃO aceitam legenda)
          - Feed imagem: apenas image_url (sem media_type)
          - Feed vídeo: entra como REELS com share_to_feed=true (é como a Meta trata hoje)
        """
        import requests
        import time

        if not self.account.meta_access_token:
            raise ValueError("Conta não possui token Meta configurado.")

        ig_user_id = self.account.ig_user_id
        if not ig_user_id:
            raise ValueError("Conta sem ig_user_id. Sincronize a conta com a Meta.")

        token = self.account.get_meta_token()
        base = f"https://graph.instagram.com/v23.0/{ig_user_id}"

        # ── Blindagem da URL (ver apps/core_utils.py) ─────────────────────
        # A Meta BAIXA a mídia desta URL. Encodar aqui — e não em quem chama —
        # garante que todo caminho de publicação fique protegido. url_segura é
        # idempotente, então não há risco de codificar duas vezes.
        from apps.core_utils import url_segura
        media_url = url_segura(media_url)
        if cover_url:
            cover_url = url_segura(cover_url)

        # Pré-checagem: se o NOSSO servidor não entrega o arquivo, a Meta
        # também não vai. Falhar aqui dá o motivo exato, em vez de esperar
        # ~50s por um "ERROR" mudo dela.
        try:
            teste = requests.get(media_url, timeout=20, stream=True,
                                 headers={'Range': 'bytes=0-1023'})
            teste.close()
            if teste.status_code >= 400:
                raise Exception(
                    f"A mídia não está acessível publicamente (HTTP {teste.status_code}). "
                    f"A Meta precisa baixá-la de: {media_url}"
                )
        except requests.RequestException as erro:
            raise Exception(f"Não consegui servir a mídia para a Meta ({erro}). URL: {media_url}")

        payload = {'access_token': token}

        if post_type == 'STORY':
            payload['media_type'] = 'STORIES'
            payload['image_url' if is_image else 'video_url'] = media_url
            # Stories não aceitam caption pela API oficial.
        elif is_image:
            payload['image_url'] = media_url
            if caption:
                payload['caption'] = caption
        else:
            payload['media_type'] = 'REELS'
            payload['video_url'] = media_url
            # No feed, o vídeo precisa aparecer na grade.
            payload['share_to_feed'] = 'true' if (share_to_feed or post_type == 'FEED') else 'false'
            if caption:
                payload['caption'] = caption
            if cover_url:
                payload['cover_url'] = cover_url

        # 1. Cria o contêiner de mídia
        res = requests.post(f"{base}/media", data=payload, timeout=30)
        data = res.json()
        if 'id' not in data:
            raise Exception(f"Erro ao criar contêiner Meta: {data.get('error', data)}")
        creation_id = data['id']

        # 2. Polling do processamento — SEMPRE, inclusive para imagem.
        # Publicar sem esperar FINISHED devolve:
        #   code 9007 / 2207027 "Media ID is not available"
        # (a imagem também passa por processamento, só que mais rápido).
        status_url = f"https://graph.instagram.com/v23.0/{creation_id}"
        # Pedimos 'status' junto: é o único campo que traz o MOTIVO da falha.
        # Só com 'status_code' o erro volta como um "ERROR" mudo.
        status_params = {'fields': 'status_code,status', 'access_token': token}
        delay = 2 if is_image else 5
        ready = False
        for _ in range(30):
            time.sleep(delay)
            status_data = requests.get(status_url, params=status_params, timeout=15).json()
            code = status_data.get('status_code')
            if code == 'FINISHED':
                ready = True
                break
            if code in ('ERROR', 'EXPIRED'):
                motivo = status_data.get('status') or code
                raise Exception(
                    f"A Meta rejeitou a mídia ({motivo}). "
                    f"URL enviada: {media_url}"
                )
        if not ready:
            raise Exception("Timeout aguardando o processamento da mídia na Meta.")

        # 3. Publica
        pub_res = requests.post(f"{base}/media_publish",
                                data={'creation_id': creation_id, 'access_token': token}, timeout=30)
        pub_data = pub_res.json()

        if 'id' not in pub_data:
            raise Exception(f"Erro ao publicar mídia via Meta: {pub_data.get('error', pub_data)}")

        return {'id': pub_data['id'], 'creation_id': creation_id}

    def midia_na_grade(self, media_id):
        """A mídia publicada foi mesmo para a grade do perfil?

        Lê `is_shared_to_feed` da própria Meta. Não dá para confiar só no que
        enviamos: a API aceita silenciosamente parâmetros que não reconhece
        (verificado — ela aceitou até um parâmetro inventado). Este campo é a
        resposta dela, não o eco do nosso pedido.
        """
        import requests

        token = self.account.get_meta_token()
        if not token or not media_id:
            return None
        try:
            dados = requests.get(
                f"https://graph.instagram.com/v23.0/{media_id}",
                params={'fields': 'is_shared_to_feed', 'access_token': token},
                timeout=15,
            ).json()
        except Exception:
            return None
        if 'error' in dados:
            return None
        return dados.get('is_shared_to_feed')

    def upload_reel_meta_api(self, video_url, caption, cover_url=None, share_to_feed=True):
        """Compatibilidade: publica um Reel via API oficial."""
        return self.publish_meta_api(
            media_url=video_url, caption=caption, post_type='REELS',
            cover_url=cover_url, share_to_feed=share_to_feed, is_image=False,
        )

    def upload_story(self, media_path, link_url=None):
        """Publica um Story pela engine (instagrapi). Diferencial: permite
        anexar LINK ao Story — a API oficial da Meta não expõe isso."""
        self._prepare_client()
        path = str(media_path)
        is_image = path.lower().endswith(self.IMAGE_EXTS)

        kwargs = {}
        if link_url:
            try:
                from instagrapi.types import StoryLink
                kwargs['links'] = [StoryLink(webUri=link_url)]
            except Exception:
                pass

        if is_image:
            media = self.client.photo_upload_to_story(path, **kwargs)
        else:
            media = self.client.video_upload_to_story(path, **kwargs)
        return media.dict()

