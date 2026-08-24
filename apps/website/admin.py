from django.contrib import admin

from .models import ContactInquiry


@admin.register(ContactInquiry)
class ContactInquiryAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "service", "created_at", "is_read")
    list_filter = ("service", "is_read")
    search_fields = ("name", "email", "message")
    readonly_fields = ("name", "email", "phone", "service", "message", "created_at")
