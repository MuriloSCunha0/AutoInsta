from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'phone', 'is_active', 'is_staff', 'created_at')
    list_filter = ('is_active', 'is_staff', 'plan_type', 'created_at')
    search_fields = ('username', 'email', 'phone')
    ordering = ('-created_at',)
    
    fieldsets = UserAdmin.fieldsets + (
        ('SandraoFlow Planos e Limites', {'fields': ('phone', 'plan_type', 'max_ig_accounts')}),
    )
