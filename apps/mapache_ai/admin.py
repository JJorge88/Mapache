from django.contrib import admin

from .models import (
    BibPhotoAnalysis,
    CombinedSearchSession,
    DetectedBib,
    FaceEmbedding,
    FaceSearchSession,
    GalleryAISettings,
    PhotoFaceIndex,
)


@admin.register(GalleryAISettings)
class GalleryAISettingsAdmin(admin.ModelAdmin):
    list_display = (
        "gallery",
        "enabled",
        "face_search_enabled",
        "bib_search_enabled",
        "face_indexing_status",
        "bib_indexing_status",
    )
    list_filter = ("enabled", "face_search_enabled", "bib_search_enabled")
    search_fields = ("gallery__title", "gallery__slug")


@admin.register(PhotoFaceIndex)
class PhotoFaceIndexAdmin(admin.ModelAdmin):
    list_display = ("photo", "gallery", "status", "face_count", "indexed_at")
    list_filter = ("status",)
    search_fields = ("photo__uuid", "gallery__title")
    readonly_fields = ("error",)


@admin.register(FaceEmbedding)
class FaceEmbeddingAdmin(admin.ModelAdmin):
    list_display = ("uuid", "gallery", "photo", "face_index", "confidence", "created_at")
    search_fields = ("uuid", "photo__uuid", "gallery__title")
    exclude = ("embedding",)
    readonly_fields = ("bounding_box",)


@admin.register(FaceSearchSession)
class FaceSearchSessionAdmin(admin.ModelAdmin):
    list_display = ("uuid", "gallery", "status", "results_count", "expires_at")
    list_filter = ("status",)
    search_fields = ("uuid", "gallery__title")
    readonly_fields = ("consent_version", "consented_at")


@admin.register(DetectedBib)
class DetectedBibAdmin(admin.ModelAdmin):
    list_display = ("uuid", "gallery", "photo", "normalized_number", "confidence", "created_at")
    list_filter = ("gallery",)
    search_fields = ("normalized_number", "photo__uuid", "gallery__title")
    readonly_fields = ("raw_text", "bounding_box")


@admin.register(BibPhotoAnalysis)
class BibPhotoAnalysisAdmin(admin.ModelAdmin):
    list_display = ("photo", "gallery", "status", "detected_count", "processed_at")
    list_filter = ("status",)
    search_fields = ("photo__uuid", "gallery__title")
    readonly_fields = ("error",)


@admin.register(CombinedSearchSession)
class CombinedSearchSessionAdmin(admin.ModelAdmin):
    list_display = (
        "uuid",
        "gallery",
        "normalized_number",
        "status",
        "results_count",
        "agreement_results_count",
        "expires_at",
    )
    list_filter = ("status",)
    search_fields = ("uuid", "gallery__title", "normalized_number")
    readonly_fields = ("consent_version", "consented_at")
