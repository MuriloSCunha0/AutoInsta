import os
import json
from django.conf import settings

class SessionManager:
    @staticmethod
    def save_session(account, client):
        session_data = client.get_settings()
        account.session_blob = session_data
        account.save()

    @staticmethod
    def load_session(account, client):
        if account.session_blob:
            client.set_settings(account.session_blob)
            return True
        return False
