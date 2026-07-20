from django.contrib import admin
from .models import InstagramAccount

@admin.register(InstagramAccount)
class InstagramAccountAdmin(admin.ModelAdmin):
    list_display = ('ig_username', 'user', 'status', 'created_at', 'updated_at')
    list_filter = ('status', 'created_at')
    search_fields = ('ig_username', 'user__username', 'user__email')
    ordering = ('-created_at',)
    readonly_fields = ('ig_username', 'ig_password', 'session_data', 'proxy_url')
    
    # Previne que o admin adicione contas pelo painel, pois requer login complexo
    def has_add_permission(self, request):
        return False
