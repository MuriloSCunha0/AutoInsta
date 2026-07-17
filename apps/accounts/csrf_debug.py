import logging
logger = logging.getLogger(__name__)

class CSRFDebugMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST":
            print("=== CSRF DEBUG ===")
            print("Scheme:", request.scheme)
            print("Host:", request.get_host())
            print("Origin:", request.headers.get("Origin"))
            print("Referer:", request.headers.get("Referer"))
            print("X-Forwarded-Proto:", request.headers.get("X-Forwarded-Proto"))
        return self.get_response(request)
