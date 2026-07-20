from django.contrib import admin
from .models import ScheduledPost

@admin.register(ScheduledPost)
class ScheduledPostAdmin(admin.ModelAdmin):
    list_display = ('id', 'owner', 'account', 'status', 'scheduled_for', 'created_at')
    list_filter = ('status', 'scheduled_for', 'created_at')
    search_fields = ('owner__username', 'account__ig_username', 'caption')
    ordering = ('-scheduled_for',)
    readonly_fields = ('video_file', 'thumbnail')
    
    # Exibe a legenda truncada na lista
    def get_caption(self, obj):
        if obj.caption:
            return obj.caption[:50] + '...' if len(obj.caption) > 50 else obj.caption
        return '-'
    get_caption.short_description = 'Legenda'
