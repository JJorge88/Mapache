from django.db.models import Count, Q

from apps.galleries.models import Gallery, Photo

from .models import BibPhotoAnalysis, DetectedBib, FaceEmbedding, GalleryAISettings, PhotoFaceIndex


def get_ai_settings(gallery: Gallery) -> GalleryAISettings:
    settings_record, _created = GalleryAISettings.objects.get_or_create(gallery=gallery)
    return settings_record


def get_ai_status(gallery: Gallery) -> dict[str, int | str | bool | None]:
    ai_settings = get_ai_settings(gallery)
    total = gallery.photos.filter(processing_status=Photo.ProcessingStatus.READY).count()
    indexed = PhotoFaceIndex.objects.filter(
        gallery=gallery, status=PhotoFaceIndex.Status.READY
    ).count()
    faces = FaceEmbedding.objects.filter(gallery=gallery).count()
    errors = PhotoFaceIndex.objects.filter(
        gallery=gallery, status=PhotoFaceIndex.Status.ERROR
    ).count()
    bib_indexed = BibPhotoAnalysis.objects.filter(
        gallery=gallery, status=BibPhotoAnalysis.Status.READY
    ).count()
    bib_errors = BibPhotoAnalysis.objects.filter(
        gallery=gallery, status=BibPhotoAnalysis.Status.ERROR
    ).count()
    bibs = DetectedBib.objects.filter(gallery=gallery).count()
    face_status = {
        "enabled": ai_settings.face_search_enabled,
        "status": ai_settings.face_indexing_status,
        "status_label": ai_settings.get_face_indexing_status_display(),
        "total": total,
        "indexed": indexed,
        "detected": faces,
        "pending": max(total - indexed - errors, 0),
        "errors": errors,
        "last_indexed_at": (
            ai_settings.face_last_indexed_at.isoformat()
            if ai_settings.face_last_indexed_at
            else None
        ),
    }
    bib_status = {
        "enabled": ai_settings.bib_search_enabled,
        "status": ai_settings.bib_indexing_status,
        "status_label": ai_settings.get_bib_indexing_status_display(),
        "total": ai_settings.bib_total_photos or total,
        "indexed": bib_indexed,
        "detected": bibs,
        "pending": max(total - bib_indexed - bib_errors, 0),
        "errors": bib_errors,
        "last_indexed_at": (
            ai_settings.bib_last_indexed_at.isoformat() if ai_settings.bib_last_indexed_at else None
        ),
    }
    return {
        "enabled": ai_settings.enabled,
        "face_search_enabled": ai_settings.face_search_enabled,
        "status": ai_settings.indexing_status,
        "status_label": ai_settings.get_indexing_status_display(),
        "total_photos": total,
        "indexed_photos": indexed,
        "faces_detected": faces,
        "pending_photos": max(total - indexed - errors, 0),
        "error_photos": errors,
        "last_indexed_at": (
            ai_settings.last_indexed_at.isoformat() if ai_settings.last_indexed_at else None
        ),
        "bib_search_enabled": ai_settings.bib_search_enabled,
        "combined_search_enabled": (
            ai_settings.enabled
            and ai_settings.face_search_enabled
            and ai_settings.bib_search_enabled
        ),
        "face": face_status,
        "bib": bib_status,
    }


def get_face_search_results(gallery: Gallery, photo_ids: list[int]):
    ordering = {photo_id: position for position, photo_id in enumerate(photo_ids)}
    photos = gallery.photos.filter(
        id__in=photo_ids,
        processing_status=Photo.ProcessingStatus.READY,
        thumbnail_file__gt="",
        optimized_file__gt="",
    )
    return sorted(photos, key=lambda photo: ordering.get(photo.id, len(ordering)))


def get_bib_search_results(gallery: Gallery, photo_ids: list[int]):
    return get_face_search_results(gallery, photo_ids)


def get_ai_gallery_summary():
    return GalleryAISettings.objects.annotate(
        face_count=Count("gallery__face_embeddings", distinct=True),
        ready_count=Count(
            "gallery__photo_face_indexes",
            filter=Q(gallery__photo_face_indexes__status=PhotoFaceIndex.Status.READY),
            distinct=True,
        ),
    )
