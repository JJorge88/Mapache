from django.contrib import admin

from .models import Gallery, GalleryDownload, GalleryUploadBatch, GalleryUploadItem, Photo


class PhotoInline(admin.TabularInline):
    model = Photo
    fields = ("original_filename", "sort_order", "processing_status", "uploaded_by")
    extra = 0
    show_change_link = True


@admin.register(Gallery)
class GalleryAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "status",
        "visibility",
        "event_date",
        "show_in_portfolio",
        "is_featured",
        "created_by",
    )
    list_filter = ("status", "visibility", "show_in_portfolio", "is_featured")
    search_fields = ("title", "slug")
    readonly_fields = ("uuid", "pin_hash", "published_at", "created_at", "updated_at")
    prepopulated_fields = {"slug": ("title",)}
    inlines = (PhotoInline,)


@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = (
        "original_filename",
        "gallery",
        "sort_order",
        "orientation",
        "processing_status",
        "file_size",
        "uploaded_by",
        "created_at",
    )
    list_filter = ("orientation", "processing_status")
    search_fields = ("original_filename", "filename", "gallery__title")
    readonly_fields = ("uuid", "created_at", "updated_at")
    list_select_related = ("gallery", "uploaded_by")


@admin.register(GalleryDownload)
class GalleryDownloadAdmin(admin.ModelAdmin):
    list_display = (
        "uuid",
        "gallery",
        "status",
        "processed_photos",
        "photo_count",
        "file_size",
        "expires_at",
    )
    list_filter = ("status",)
    search_fields = ("uuid", "gallery__title", "gallery__slug")
    readonly_fields = (
        "uuid",
        "content_fingerprint",
        "authorization_hash",
        "requested_at",
        "started_at",
        "completed_at",
        "expires_at",
        "created_at",
        "updated_at",
    )
    list_select_related = ("gallery",)


@admin.register(GalleryUploadBatch)
class GalleryUploadBatchAdmin(admin.ModelAdmin):
    list_display = ("uuid", "gallery", "created_by", "status", "completed_files", "total_files")
    list_filter = ("status",)
    search_fields = ("uuid", "gallery__title", "created_by__email")
    readonly_fields = ("uuid", "created_at", "updated_at")
    list_select_related = ("gallery", "created_by")


@admin.register(GalleryUploadItem)
class GalleryUploadItemAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "gallery", "upload_mode", "status", "expected_size")
    list_filter = ("upload_mode", "status")
    search_fields = ("uuid", "original_filename", "gallery__title")
    readonly_fields = ("uuid", "object_key", "multipart_upload_id", "created_at", "updated_at")
    list_select_related = ("gallery", "batch", "photo")
