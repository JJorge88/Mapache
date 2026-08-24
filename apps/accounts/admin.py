from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class MapacheUserAdmin(UserAdmin):
    fieldsets = (*UserAdmin.fieldsets, ("Mapache Studio", {"fields": ("role", "avatar")}))
    add_fieldsets = (*UserAdmin.add_fieldsets, ("Mapache Studio", {"fields": ("role", "avatar")}))
    list_display = (*UserAdmin.list_display, "role")
    list_filter = (*UserAdmin.list_filter, "role")
