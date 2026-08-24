import logging
import math
import time
from collections.abc import Iterable

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from pgvector.django import CosineDistance

from apps.audit.models import AuditLog
from apps.galleries.models import Gallery, Photo

from .engines import get_face_engine
from .engines.base import FaceEngine
from .exceptions import MultipleFacesDetected, NoFaceDetected
from .models import FaceEmbedding, FaceSearchSession, GalleryAISettings, PhotoFaceIndex
from .selectors import get_ai_settings

logger = logging.getLogger("mapache.ai")


def _audit(*, user, action: str, gallery: Gallery) -> None:
    AuditLog.objects.create(
        user=user,
        action=action,
        model_name="Gallery",
        object_id=str(gallery.uuid),
        metadata={},
    )


def _validate_embedding(embedding: Iterable[float], engine: FaceEngine) -> list[float]:
    vector = [float(value) for value in embedding]
    expected = settings.MAPACHE_FACE_EMBEDDING_DIMENSION
    if engine.embedding_dimension != expected or len(vector) != expected:
        raise ValidationError(
            "La dimensión de la representación facial no coincide con el modelo configurado."
        )
    return vector


@transaction.atomic
def configure_gallery_ai(
    *,
    gallery: Gallery,
    enabled: bool,
    face_search_enabled: bool,
    bib_search_enabled: bool,
    bib_format: str,
    bib_min_length: int,
    bib_max_length: int,
    changed_by,
) -> GalleryAISettings:
    ai_settings = get_ai_settings(gallery)
    previous_enabled = ai_settings.enabled
    previous_bib_enabled = ai_settings.bib_search_enabled
    ai_settings.enabled = enabled
    ai_settings.face_search_enabled = enabled and face_search_enabled
    ai_settings.bib_search_enabled = enabled and bib_search_enabled
    ai_settings.bib_format = bib_format
    ai_settings.bib_min_length = bib_min_length
    ai_settings.bib_max_length = bib_max_length
    face_status = (
        GalleryAISettings.IndexingStatus.PENDING
        if enabled and face_search_enabled
        else GalleryAISettings.IndexingStatus.DISABLED
    )
    bib_status = (
        GalleryAISettings.IndexingStatus.PENDING
        if enabled and bib_search_enabled
        else GalleryAISettings.IndexingStatus.DISABLED
    )
    ai_settings.indexing_status = face_status
    ai_settings.face_indexing_status = face_status
    ai_settings.bib_indexing_status = bib_status
    ai_settings.save(
        update_fields=[
            "enabled",
            "face_search_enabled",
            "bib_search_enabled",
            "bib_format",
            "bib_min_length",
            "bib_max_length",
            "indexing_status",
            "face_indexing_status",
            "bib_indexing_status",
            "updated_at",
        ]
    )
    action = "MAPACHE_AI_ENABLED" if enabled else "MAPACHE_AI_DISABLED"
    if enabled != previous_enabled:
        _audit(user=changed_by, action=action, gallery=gallery)
    if ai_settings.bib_search_enabled != previous_bib_enabled:
        _audit(
            user=changed_by,
            action=(
                "MAPACHE_BIB_SEARCH_ENABLED"
                if ai_settings.bib_search_enabled
                else "MAPACHE_BIB_SEARCH_DISABLED"
            ),
            gallery=gallery,
        )
    if ai_settings.face_search_enabled:
        from .tasks import index_gallery_faces

        transaction.on_commit(lambda: index_gallery_faces.delay(gallery.pk))
    if ai_settings.bib_search_enabled:
        from .bib.tasks import index_gallery_bibs

        transaction.on_commit(lambda: index_gallery_bibs.delay(gallery.pk))
    return ai_settings


@transaction.atomic
def request_gallery_reindex(*, gallery: Gallery, requested_by) -> GalleryAISettings:
    ai_settings = get_ai_settings(gallery)
    if not ai_settings.enabled or not ai_settings.face_search_enabled:
        raise ValidationError("Activa la búsqueda por rostro antes de reindexarla.")
    ai_settings.indexing_status = GalleryAISettings.IndexingStatus.PENDING
    ai_settings.face_indexing_status = GalleryAISettings.IndexingStatus.PENDING
    ai_settings.save(update_fields=["indexing_status", "face_indexing_status", "updated_at"])
    _audit(user=requested_by, action="MAPACHE_AI_REINDEX_REQUESTED", gallery=gallery)
    from .tasks import index_gallery_faces

    transaction.on_commit(lambda: index_gallery_faces.delay(gallery.pk))
    return ai_settings


@transaction.atomic
def delete_gallery_face_index(*, gallery: Gallery, deleted_by) -> int:
    deleted, _details = FaceEmbedding.objects.filter(gallery=gallery).delete()
    PhotoFaceIndex.objects.filter(gallery=gallery).delete()
    ai_settings = get_ai_settings(gallery)
    ai_settings.indexed_photos = 0
    ai_settings.total_photos = gallery.photos.filter(
        processing_status=Photo.ProcessingStatus.READY
    ).count()
    ai_settings.last_indexed_at = None
    ai_settings.face_indexed_photos = 0
    ai_settings.face_total_photos = ai_settings.total_photos
    ai_settings.face_last_indexed_at = None
    ai_settings.indexing_status = (
        GalleryAISettings.IndexingStatus.PENDING
        if ai_settings.enabled and ai_settings.face_search_enabled
        else GalleryAISettings.IndexingStatus.DISABLED
    )
    ai_settings.face_indexing_status = ai_settings.indexing_status
    ai_settings.save(
        update_fields=[
            "indexed_photos",
            "total_photos",
            "last_indexed_at",
            "indexing_status",
            "face_indexed_photos",
            "face_total_photos",
            "face_last_indexed_at",
            "face_indexing_status",
            "updated_at",
        ]
    )
    _audit(user=deleted_by, action="GALLERY_FACE_INDEX_DELETED", gallery=gallery)
    return deleted


def refresh_gallery_index_progress(gallery_id: int) -> None:
    try:
        ai_settings = GalleryAISettings.objects.get(gallery_id=gallery_id)
    except GalleryAISettings.DoesNotExist:
        return
    total = Photo.objects.filter(
        gallery_id=gallery_id, processing_status=Photo.ProcessingStatus.READY
    ).count()
    states = PhotoFaceIndex.objects.filter(gallery_id=gallery_id)
    indexed = states.filter(status=PhotoFaceIndex.Status.READY).count()
    errors = states.filter(status=PhotoFaceIndex.Status.ERROR).count()
    active = states.filter(
        status__in=[PhotoFaceIndex.Status.PENDING, PhotoFaceIndex.Status.INDEXING]
    ).count()
    ai_settings.total_photos = total
    ai_settings.indexed_photos = indexed
    if not ai_settings.enabled or not ai_settings.face_search_enabled:
        ai_settings.indexing_status = GalleryAISettings.IndexingStatus.DISABLED
    elif active:
        ai_settings.indexing_status = GalleryAISettings.IndexingStatus.INDEXING
    elif errors:
        ai_settings.indexing_status = GalleryAISettings.IndexingStatus.ERROR
    else:
        ai_settings.indexing_status = GalleryAISettings.IndexingStatus.READY
        ai_settings.last_indexed_at = timezone.now()
    ai_settings.face_total_photos = total
    ai_settings.face_indexed_photos = indexed
    ai_settings.face_indexing_status = ai_settings.indexing_status
    ai_settings.face_last_indexed_at = ai_settings.last_indexed_at
    ai_settings.save(
        update_fields=[
            "total_photos",
            "indexed_photos",
            "indexing_status",
            "last_indexed_at",
            "face_total_photos",
            "face_indexed_photos",
            "face_indexing_status",
            "face_last_indexed_at",
            "updated_at",
        ]
    )


def prepare_gallery_index(gallery_id: int) -> list[int]:
    try:
        ai_settings = GalleryAISettings.objects.select_related("gallery").get(
            gallery_id=gallery_id, enabled=True, face_search_enabled=True
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
    PhotoFaceIndex.objects.filter(gallery_id=gallery_id, photo_id__in=photo_ids).update(
        status=PhotoFaceIndex.Status.PENDING, error=""
    )
    existing = set(
        PhotoFaceIndex.objects.filter(gallery_id=gallery_id, photo_id__in=photo_ids).values_list(
            "photo_id", flat=True
        )
    )
    PhotoFaceIndex.objects.bulk_create(
        [
            PhotoFaceIndex(
                gallery_id=gallery_id, photo_id=photo_id, status=PhotoFaceIndex.Status.PENDING
            )
            for photo_id in photo_ids
            if photo_id not in existing
        ]
    )
    ai_settings.total_photos = len(photo_ids)
    ai_settings.indexed_photos = 0
    ai_settings.indexing_status = GalleryAISettings.IndexingStatus.INDEXING
    if not photo_ids:
        ai_settings.indexing_status = GalleryAISettings.IndexingStatus.READY
        ai_settings.last_indexed_at = timezone.now()
    ai_settings.face_total_photos = len(photo_ids)
    ai_settings.face_indexed_photos = 0
    ai_settings.face_indexing_status = ai_settings.indexing_status
    ai_settings.face_last_indexed_at = ai_settings.last_indexed_at
    ai_settings.save(
        update_fields=[
            "total_photos",
            "indexed_photos",
            "indexing_status",
            "last_indexed_at",
            "face_total_photos",
            "face_indexed_photos",
            "face_indexing_status",
            "face_last_indexed_at",
            "updated_at",
        ]
    )
    return photo_ids


def index_photo_faces_now(photo_id: int, *, engine: FaceEngine | None = None) -> int:
    started = time.monotonic()
    try:
        photo = Photo.objects.select_related("gallery").get(pk=photo_id)
        ai_settings = GalleryAISettings.objects.get(
            gallery=photo.gallery, enabled=True, face_search_enabled=True
        )
    except (Photo.DoesNotExist, GalleryAISettings.DoesNotExist):
        return 0
    if photo.processing_status != Photo.ProcessingStatus.READY or not photo.optimized_file:
        return 0
    state, _created = PhotoFaceIndex.objects.get_or_create(gallery=photo.gallery, photo=photo)
    state.status = PhotoFaceIndex.Status.INDEXING
    state.error = ""
    state.save(update_fields=["status", "error", "updated_at"])
    engine = engine or get_face_engine()
    try:
        with photo.optimized_file.storage.open(photo.optimized_file.name, "rb") as source:
            image_bytes = source.read()
        faces = engine.detect_faces(image_bytes)
        generated = [
            (
                index,
                _validate_embedding(engine.generate_embedding(face), engine),
                face,
            )
            for index, face in enumerate(faces)
        ]
        with transaction.atomic():
            current = Photo.objects.select_for_update().get(pk=photo.pk)
            current_settings = GalleryAISettings.objects.get(pk=ai_settings.pk)
            if (
                current.processing_status != Photo.ProcessingStatus.READY
                or not current_settings.enabled
                or not current_settings.face_search_enabled
            ):
                return 0
            FaceEmbedding.objects.filter(photo=current).delete()
            FaceEmbedding.objects.bulk_create(
                [
                    FaceEmbedding(
                        gallery=current.gallery,
                        photo=current,
                        face_index=index,
                        embedding=embedding,
                        confidence=face.confidence,
                        bounding_box=face.bounding_box,
                    )
                    for index, embedding, face in generated
                ]
            )
            state = PhotoFaceIndex.objects.select_for_update().get(photo=current)
            state.status = PhotoFaceIndex.Status.READY
            state.face_count = len(generated)
            state.error = ""
            state.indexed_at = timezone.now()
            state.save(update_fields=["status", "face_count", "error", "indexed_at", "updated_at"])
    except Exception as exc:
        state.status = PhotoFaceIndex.Status.ERROR
        state.error = "No fue posible indexar esta fotografía."  # Never persist provider details.
        state.save(update_fields=["status", "error", "updated_at"])
        refresh_gallery_index_progress(photo.gallery_id)
        logger.exception("Face indexing failed gallery=%s photo=%s", photo.gallery.uuid, photo.uuid)
        raise exc
    refresh_gallery_index_progress(photo.gallery_id)
    logger.info(
        "Face indexing finished gallery=%s photo=%s faces=%s duration_ms=%s",
        photo.gallery.uuid,
        photo.uuid,
        len(generated),
        round((time.monotonic() - started) * 1000),
    )
    return len(generated)


def search_faces_in_gallery(
    *,
    gallery: Gallery,
    query_embedding: Iterable[float],
    limit: int | None = None,
    threshold: float | None = None,
) -> list[int]:
    vector = [float(value) for value in query_embedding]
    if len(vector) != settings.MAPACHE_FACE_EMBEDDING_DIMENSION:
        raise ValidationError("La representación facial de consulta no es válida.")
    limit = limit or settings.MAPACHE_FACE_SEARCH_LIMIT
    similarity_threshold = (
        threshold if threshold is not None else settings.MAPACHE_FACE_MATCH_THRESHOLD
    )
    maximum_distance = 1.0 - similarity_threshold
    candidates = (
        FaceEmbedding.objects.filter(
            gallery_id=gallery.id,
            photo__gallery_id=gallery.id,
            photo__processing_status=Photo.ProcessingStatus.READY,
            photo__thumbnail_file__gt="",
            photo__optimized_file__gt="",
        )
        .annotate(distance=CosineDistance("embedding", vector))
        .filter(distance__lte=maximum_distance)
        .order_by("distance", "photo_id")
        .values("photo_id", "embedding", "distance")
    )
    candidate_rows = list(candidates[: max(limit * 10, limit)])

    # Explicit thresholds retain the simple, predictable search used by callers
    # that are calibrating or testing a custom cut-off. The default public search
    # adds a confirmation pass: a borderline face is accepted only when a strong
    # direct match from this same query also recognizes it.
    if threshold is None:
        strong_threshold = max(
            similarity_threshold,
            settings.MAPACHE_FACE_STRONG_MATCH_THRESHOLD,
        )
        strong_embeddings = [
            row["embedding"]
            for row in candidate_rows
            if 1.0 - float(row["distance"]) >= strong_threshold
        ]
        candidate_rows = [
            row
            for row in candidate_rows
            if 1.0 - float(row["distance"]) >= strong_threshold
            or any(
                _cosine_similarity(row["embedding"], seed) >= strong_threshold
                for seed in strong_embeddings
            )
        ]

    result = []
    seen = set()
    for row in candidate_rows:
        photo_id = row["photo_id"]
        if photo_id in seen:
            continue
        seen.add(photo_id)
        result.append(photo_id)
        if len(result) >= limit:
            break
    return result


def _cosine_similarity(first: Iterable[float], second: Iterable[float]) -> float:
    left = [float(value) for value in first]
    right = [float(value) for value in second]
    if len(left) != len(right):
        return -1.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return -1.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def results_cache_key(session_uuid) -> str:
    return f"mapache:face-search:{session_uuid}"


def run_face_query(
    *, gallery: Gallery, image_bytes: bytes, engine: FaceEngine | None = None
) -> list[int]:
    started = time.monotonic()
    engine = engine or get_face_engine()
    faces = engine.detect_faces(image_bytes)
    if not faces:
        raise NoFaceDetected
    if len(faces) > 1:
        raise MultipleFacesDetected
    query_embedding = _validate_embedding(engine.generate_embedding(faces[0]), engine)
    result = search_faces_in_gallery(gallery=gallery, query_embedding=query_embedding)
    logger.info(
        "Face search finished gallery=%s results=%s duration_ms=%s",
        gallery.uuid,
        len(result),
        round((time.monotonic() - started) * 1000),
    )
    return result


def create_search_session(*, gallery: Gallery) -> FaceSearchSession:
    return FaceSearchSession.objects.create(
        gallery=gallery,
        consent_version=settings.MAPACHE_FACE_CONSENT_VERSION,
        consented_at=timezone.now(),
        status=FaceSearchSession.Status.PROCESSING,
        expires_at=FaceSearchSession.new_expiration(),
    )


def complete_search_session(session: FaceSearchSession, photo_ids: list[int]) -> None:
    ttl = max(int((session.expires_at - timezone.now()).total_seconds()), 1)
    cache.set(results_cache_key(session.uuid), photo_ids, timeout=ttl)
    session.status = FaceSearchSession.Status.COMPLETED
    session.results_count = len(photo_ids)
    session.save(update_fields=["status", "results_count"])


def check_search_rate_limit(identifier: str) -> bool:
    key = f"mapache:face-rate:{identifier}"
    window = settings.MAPACHE_FACE_SEARCH_RATE_WINDOW
    if cache.add(key, 1, timeout=window):
        return True
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window)
        return True
    return count <= settings.MAPACHE_FACE_SEARCH_RATE_LIMIT
