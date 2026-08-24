import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Case, IntegerField, Max, Min, Q, Value, When
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.galleries.models import Gallery, Photo
from apps.mapache_ai.models import (
    BibPhotoAnalysis,
    BibSearchSession,
    DetectedBib,
    GalleryAISettings,
)

from .base import BibRecognitionEngine, RecognizedBib
from .engines import get_bib_engine
from .normalization import normalize_bib_text

logger = logging.getLogger("mapache.ai")


def _audit(*, user, action: str, gallery: Gallery) -> None:
    AuditLog.objects.create(
        user=user,
        action=action,
        model_name="Gallery",
        object_id=str(gallery.uuid),
        metadata={},
    )


def _iou(first: dict[str, float], second: dict[str, float]) -> float:
    left = max(first["x"], second["x"])
    top = max(first["y"], second["y"])
    right = min(first["x"] + first["width"], second["x"] + second["width"])
    bottom = min(first["y"] + first["height"], second["y"] + second["height"])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    union = first["width"] * first["height"] + second["width"] * second["height"] - intersection
    return intersection / union if union else 0.0


def _valid_detections(
    detections: list[RecognizedBib], ai_settings: GalleryAISettings
) -> list[tuple[RecognizedBib, str]]:
    accepted: list[tuple[RecognizedBib, str]] = []
    for detection in sorted(detections, key=lambda item: item.confidence, reverse=True):
        if detection.confidence < settings.MAPACHE_BIB_MIN_CONFIDENCE:
            continue
        normalized = normalize_bib_text(
            detection.raw_text,
            bib_format=ai_settings.bib_format,
            min_length=ai_settings.bib_min_length,
            max_length=ai_settings.bib_max_length,
        )
        if not normalized:
            continue
        duplicate = any(
            prior_number == normalized and _iou(prior.bounding_box, detection.bounding_box) >= 0.35
            for prior, prior_number in accepted
        )
        if not duplicate:
            accepted.append((detection, normalized))
    return accepted


def refresh_gallery_bib_progress(gallery_id: int) -> None:
    try:
        ai_settings = GalleryAISettings.objects.get(gallery_id=gallery_id)
    except GalleryAISettings.DoesNotExist:
        return
    total = Photo.objects.filter(
        gallery_id=gallery_id,
        processing_status=Photo.ProcessingStatus.READY,
        optimized_file__gt="",
    ).count()
    analyses = BibPhotoAnalysis.objects.filter(gallery_id=gallery_id)
    indexed = analyses.filter(status=BibPhotoAnalysis.Status.READY).count()
    errors = analyses.filter(status=BibPhotoAnalysis.Status.ERROR).count()
    active = analyses.filter(
        status__in=[BibPhotoAnalysis.Status.PENDING, BibPhotoAnalysis.Status.INDEXING]
    ).count()
    ai_settings.bib_total_photos = total
    ai_settings.bib_indexed_photos = indexed
    if not ai_settings.enabled or not ai_settings.bib_search_enabled:
        ai_settings.bib_indexing_status = GalleryAISettings.IndexingStatus.DISABLED
    elif active:
        ai_settings.bib_indexing_status = GalleryAISettings.IndexingStatus.INDEXING
    elif errors:
        ai_settings.bib_indexing_status = GalleryAISettings.IndexingStatus.ERROR
    else:
        ai_settings.bib_indexing_status = GalleryAISettings.IndexingStatus.READY
        ai_settings.bib_last_indexed_at = timezone.now()
    ai_settings.save(
        update_fields=[
            "bib_total_photos",
            "bib_indexed_photos",
            "bib_indexing_status",
            "bib_last_indexed_at",
            "updated_at",
        ]
    )


def prepare_gallery_bib_index(gallery_id: int) -> list[int]:
    try:
        ai_settings = GalleryAISettings.objects.get(
            gallery_id=gallery_id, enabled=True, bib_search_enabled=True
        )
    except GalleryAISettings.DoesNotExist:
        return []
    photo_ids = list(
        Photo.objects.filter(
            gallery_id=gallery_id,
            processing_status=Photo.ProcessingStatus.READY,
            optimized_file__gt="",
        ).values_list("id", flat=True)
    )
    existing = set(
        BibPhotoAnalysis.objects.filter(photo_id__in=photo_ids).values_list("photo_id", flat=True)
    )
    BibPhotoAnalysis.objects.filter(photo_id__in=photo_ids).update(
        status=BibPhotoAnalysis.Status.PENDING, error=""
    )
    BibPhotoAnalysis.objects.bulk_create(
        [
            BibPhotoAnalysis(gallery_id=gallery_id, photo_id=photo_id)
            for photo_id in photo_ids
            if photo_id not in existing
        ]
    )
    ai_settings.bib_total_photos = len(photo_ids)
    ai_settings.bib_indexed_photos = 0
    ai_settings.bib_indexing_status = (
        GalleryAISettings.IndexingStatus.INDEXING
        if photo_ids
        else GalleryAISettings.IndexingStatus.READY
    )
    if not photo_ids:
        ai_settings.bib_last_indexed_at = timezone.now()
    ai_settings.save(
        update_fields=[
            "bib_total_photos",
            "bib_indexed_photos",
            "bib_indexing_status",
            "bib_last_indexed_at",
            "updated_at",
        ]
    )
    return photo_ids


def index_photo_bibs_now(photo_id: int, *, engine: BibRecognitionEngine | None = None) -> int:
    started = time.monotonic()
    try:
        photo = Photo.objects.select_related("gallery").get(pk=photo_id)
        ai_settings = GalleryAISettings.objects.get(
            gallery=photo.gallery, enabled=True, bib_search_enabled=True
        )
    except (Photo.DoesNotExist, GalleryAISettings.DoesNotExist):
        return 0
    if photo.processing_status != Photo.ProcessingStatus.READY or not photo.optimized_file:
        return 0
    analysis, _created = BibPhotoAnalysis.objects.get_or_create(gallery=photo.gallery, photo=photo)
    analysis.status = BibPhotoAnalysis.Status.INDEXING
    analysis.error = ""
    analysis.save(update_fields=["status", "error", "updated_at"])
    try:
        with photo.optimized_file.storage.open(photo.optimized_file.name, "rb") as source:
            image_bytes = source.read()
        detections = (engine or get_bib_engine()).recognize_bibs(
            image_bytes, bib_format=ai_settings.bib_format
        )
        accepted = _valid_detections(detections, ai_settings)
        with transaction.atomic():
            current = Photo.objects.select_for_update().get(pk=photo.pk)
            current_settings = GalleryAISettings.objects.get(pk=ai_settings.pk)
            if (
                current.processing_status != Photo.ProcessingStatus.READY
                or not current_settings.enabled
                or not current_settings.bib_search_enabled
            ):
                return 0
            DetectedBib.objects.filter(photo=current).delete()
            DetectedBib.objects.bulk_create(
                [
                    DetectedBib(
                        gallery=current.gallery,
                        photo=current,
                        raw_text=detection.raw_text[:64],
                        normalized_number=normalized,
                        confidence=detection.confidence,
                        bounding_box=detection.bounding_box,
                    )
                    for detection, normalized in accepted
                ]
            )
            locked = BibPhotoAnalysis.objects.select_for_update().get(photo=current)
            locked.status = BibPhotoAnalysis.Status.READY
            locked.detected_count = len(accepted)
            locked.error = ""
            locked.processed_at = timezone.now()
            locked.save(
                update_fields=["status", "detected_count", "error", "processed_at", "updated_at"]
            )
    except Exception:
        analysis.status = BibPhotoAnalysis.Status.ERROR
        analysis.error = "No fue posible analizar los dorsales de esta fotografía."
        analysis.save(update_fields=["status", "error", "updated_at"])
        refresh_gallery_bib_progress(photo.gallery_id)
        logger.exception("Bib indexing failed gallery=%s photo=%s", photo.gallery.uuid, photo.uuid)
        raise
    refresh_gallery_bib_progress(photo.gallery_id)
    logger.info(
        "Bib indexing finished gallery=%s photo=%s bibs=%s duration_ms=%s",
        photo.gallery.uuid,
        photo.uuid,
        len(accepted),
        round((time.monotonic() - started) * 1000),
    )
    return len(accepted)


def search_bibs_in_gallery(*, gallery: Gallery, query_number: str, limit: int | None = None):
    try:
        ai_settings = GalleryAISettings.objects.get(
            gallery=gallery, enabled=True, bib_search_enabled=True
        )
    except GalleryAISettings.DoesNotExist as exc:
        raise ValidationError("La búsqueda por número no está habilitada.") from exc
    normalized = normalize_bib_text(
        query_number,
        bib_format=ai_settings.bib_format,
        min_length=ai_settings.bib_min_length,
        max_length=ai_settings.bib_max_length,
    )
    if not normalized:
        raise ValidationError("Escribe un número con el formato configurado para este evento.")
    number_filter = Q(normalized_number=normalized)
    numeric_search = ai_settings.bib_format == GalleryAISettings.BibFormat.NUMERIC
    canonical_number_filter = Q()
    if numeric_search:
        canonical = normalized.lstrip("0") or "0"
        canonical_number_filter = Q(normalized_number__regex=rf"^0*{canonical}$")
        number_filter |= canonical_number_filter
    partial_single_digit = numeric_search and len(normalized) == 1
    if partial_single_digit:
        number_filter |= Q(normalized_number__contains=normalized)
    priority_cases = [When(normalized_number=normalized, then=Value(0))]
    if numeric_search:
        priority_cases.append(When(canonical_number_filter, then=Value(1)))

    candidates = (
        DetectedBib.objects.filter(
            number_filter,
            gallery_id=gallery.id,
            photo__gallery_id=gallery.id,
            photo__processing_status=Photo.ProcessingStatus.READY,
            photo__thumbnail_file__gt="",
            photo__optimized_file__gt="",
        )
        .annotate(
            match_priority=Case(
                *priority_cases,
                default=Value(2),
                output_field=IntegerField(),
            )
        )
        .values("photo_id", "photo__sort_order")
        .annotate(best_confidence=Max("confidence"), best_priority=Min("match_priority"))
        .order_by("best_priority", "-best_confidence", "photo__sort_order", "photo_id")
    )
    result_limit = limit or settings.MAPACHE_BIB_SEARCH_LIMIT
    return normalized, [row["photo_id"] for row in candidates[:result_limit]]


def bib_results_cache_key(session_uuid) -> str:
    return f"mapache:bib-search:{session_uuid}"


def create_bib_search_session(
    *, gallery: Gallery, normalized_number: str, photo_ids: list[int]
) -> BibSearchSession:
    session = BibSearchSession.objects.create(
        gallery=gallery,
        normalized_number=normalized_number,
        results_count=len(photo_ids),
        expires_at=BibSearchSession.new_expiration(),
    )
    ttl = max(int((session.expires_at - timezone.now()).total_seconds()), 1)
    cache.set(bib_results_cache_key(session.uuid), photo_ids, timeout=ttl)
    return session


def check_bib_search_rate_limit(identifier: str) -> bool:
    key = f"mapache:bib-rate:{identifier}"
    if cache.add(key, 1, timeout=settings.MAPACHE_BIB_SEARCH_RATE_WINDOW):
        return True
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=settings.MAPACHE_BIB_SEARCH_RATE_WINDOW)
        return True
    return count <= settings.MAPACHE_BIB_SEARCH_RATE_LIMIT


@transaction.atomic
def request_gallery_bib_reindex(*, gallery: Gallery, requested_by) -> GalleryAISettings:
    ai_settings = GalleryAISettings.objects.get(gallery=gallery)
    if not ai_settings.enabled or not ai_settings.bib_search_enabled:
        raise ValidationError("Activa la búsqueda por número antes de reindexarla.")
    ai_settings.bib_indexing_status = GalleryAISettings.IndexingStatus.PENDING
    ai_settings.save(update_fields=["bib_indexing_status", "updated_at"])
    _audit(user=requested_by, action="MAPACHE_BIB_REINDEX_REQUESTED", gallery=gallery)
    from .tasks import index_gallery_bibs

    transaction.on_commit(lambda: index_gallery_bibs.delay(gallery.pk))
    return ai_settings


@transaction.atomic
def delete_gallery_bib_index(*, gallery: Gallery, deleted_by) -> int:
    deleted, _details = DetectedBib.objects.filter(gallery=gallery).delete()
    BibPhotoAnalysis.objects.filter(gallery=gallery).delete()
    ai_settings = GalleryAISettings.objects.get(gallery=gallery)
    ai_settings.bib_indexed_photos = 0
    ai_settings.bib_total_photos = gallery.photos.filter(
        processing_status=Photo.ProcessingStatus.READY, optimized_file__gt=""
    ).count()
    ai_settings.bib_last_indexed_at = None
    ai_settings.bib_indexing_status = (
        GalleryAISettings.IndexingStatus.PENDING
        if ai_settings.enabled and ai_settings.bib_search_enabled
        else GalleryAISettings.IndexingStatus.DISABLED
    )
    ai_settings.save(
        update_fields=[
            "bib_indexed_photos",
            "bib_total_photos",
            "bib_last_indexed_at",
            "bib_indexing_status",
            "updated_at",
        ]
    )
    _audit(user=deleted_by, action="GALLERY_BIB_INDEX_DELETED", gallery=gallery)
    return deleted
