from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "model_name", "object_id", "user")
    list_filter = ("action", "model_name", "created_at")
    search_fields = ("action", "model_name", "object_id", "user__username")
    readonly_fields = ("user", "action", "model_name", "object_id", "metadata", "created_at")
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
