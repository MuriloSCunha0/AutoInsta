from django.contrib import admin
from .models import Post

@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'account', 'post_type', 'status', 'scheduled_for', 'created_at')
    list_filter = ('status', 'post_type', 'scheduled_for', 'created_at')
    search_fields = ('user__username', 'account__ig_username', 'caption')
    ordering = ('-scheduled_for',)
    readonly_fields = ('media_file', 'celery_task_id')
    
    # Exibe a legenda truncada na lista
    def get_caption(self, obj):
        if obj.caption:
            return obj.caption[:50] + '...' if len(obj.caption) > 50 else obj.caption
        return '-'
    get_caption.short_description = 'Legenda'
