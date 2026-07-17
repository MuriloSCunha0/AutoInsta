class SessionManager:
    @staticmethod
    def save_session(account, client):
        """Persiste a sessão completa (cookies + device) para reaproveitar."""
        settings = client.get_settings()
        account.session_blob = settings
        # Guarda também o device isolado, para reusá-lo mesmo quando a
        # sessão expira e precisamos relogar do zero.
        account.device_settings = {
            'device_settings': settings.get('device_settings'),
            'uuids': settings.get('uuids'),
            'user_agent': settings.get('user_agent'),
        }
        account.save()

    @staticmethod
    def load_session(account, client):
        """Carrega a sessão salva. Retorna True se havia sessão."""
        if account.session_blob:
            client.set_settings(account.session_blob)
            return True
        return False

    @staticmethod
    def ensure_device(account, client):
        """Garante um device ESTÁVEL por conta.

        Sem isso, cada tentativa de login gera um aparelho novo e o Instagram
        trata como 'dispositivo desconhecido logando' → responde bad_password.
        Fixar o device por conta é o que mais reduz o falso 'senha incorreta'.
        """
        if account.device_settings and account.device_settings.get('device_settings'):
            current = client.get_settings()
            current['device_settings'] = account.device_settings['device_settings']
            if account.device_settings.get('uuids'):
                current['uuids'] = account.device_settings['uuids']
            client.set_settings(current)
            ua = account.device_settings.get('user_agent')
            if ua:
                client.set_user_agent(ua)
        else:
            # Primeiro login: persiste o device recém-gerado para reusar sempre.
            s = client.get_settings()
            account.device_settings = {
                'device_settings': s.get('device_settings'),
                'uuids': s.get('uuids'),
                'user_agent': s.get('user_agent'),
            }
            account.save(update_fields=['device_settings'])
