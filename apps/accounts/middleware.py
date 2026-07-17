from django.contrib.auth import login
from django.contrib.auth import get_user_model

User = get_user_model()

class AutoLoginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            try:
                # Loga o primeiro usuario encontrado (normalmente o admin criado no entrypoint)
                user = User.objects.first()
                if user:
                    login(request, user)
            except Exception:
                pass
        
        return self.get_response(request)
