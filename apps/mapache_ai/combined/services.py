from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.galleries.models import Gallery
from apps.mapache_ai.bib.services import search_bibs_in_gallery
from apps.mapache_ai.engines.base import FaceEngine
from apps.mapache_ai.models import CombinedSearchSession, GalleryAISettings
from apps.mapache_ai.services import run_face_query


@dataclass(frozen=True)
class CombinedSearchResult:
    photo_id: int
    face_score: float
    bib_score: float
    combined_score: float
    matched_face: bool
    matched_bib: bool


def rank_combined_results(
    *,
    face_photo_ids: list[int],
    bib_photo_ids: list[int],
    face_weight: float | None = None,
    bib_weight: float | None = None,
    rrf_k: int | None = None,
    limit: int | None = None,
) -> list[CombinedSearchResult]:
    face_weight = settings.MAPACHE_COMBINED_FACE_WEIGHT if face_weight is None else face_weight
    bib_weight = settings.MAPACHE_COMBINED_BIB_WEIGHT if bib_weight is None else bib_weight
    if face_weight < 0 or bib_weight < 0 or face_weight + bib_weight <= 0:
        raise ValidationError("Los pesos de búsqueda combinada no son válidos.")
    weight_total = face_weight + bib_weight
    face_weight /= weight_total
    bib_weight /= weight_total
    rrf_k = settings.MAPACHE_COMBINED_RRF_K if rrf_k is None else rrf_k
    if rrf_k < 1:
        raise ValidationError("La constante RRF debe ser positiva.")

    face_ranks = {photo_id: rank for rank, photo_id in enumerate(face_photo_ids, start=1)}
    bib_ranks = {photo_id: rank for rank, photo_id in enumerate(bib_photo_ids, start=1)}
    candidates = set(face_ranks) | set(bib_ranks)
    results = []
    for photo_id in candidates:
        face_score = 1 / (rrf_k + face_ranks[photo_id]) if photo_id in face_ranks else 0.0
        bib_score = 1 / (rrf_k + bib_ranks[photo_id]) if photo_id in bib_ranks else 0.0
        results.append(
            CombinedSearchResult(
                photo_id=photo_id,
                face_score=face_score,
                bib_score=bib_score,
                combined_score=face_weight * face_score + bib_weight * bib_score,
                matched_face=photo_id in face_ranks,
                matched_bib=photo_id in bib_ranks,
            )
        )
    results.sort(
        key=lambda result: (
            -result.combined_score,
            -(int(result.matched_face) + int(result.matched_bib)),
            result.photo_id,
        )
    )
    return results[: limit or settings.MAPACHE_COMBINED_SEARCH_LIMIT]


def search_combined_in_gallery(
    *,
    gallery: Gallery,
    image_bytes: bytes,
    query_number: str,
    face_engine: FaceEngine | None = None,
    limit: int | None = None,
) -> tuple[str, list[CombinedSearchResult], int, int]:
    try:
        GalleryAISettings.objects.get(
            gallery=gallery,
            enabled=True,
            face_search_enabled=True,
            bib_search_enabled=True,
        )
    except GalleryAISettings.DoesNotExist as exc:
        raise ValidationError("La búsqueda combinada no está habilitada.") from exc
    normalized_number, bib_photo_ids = search_bibs_in_gallery(
        gallery=gallery, query_number=query_number
    )
    face_photo_ids = run_face_query(gallery=gallery, image_bytes=image_bytes, engine=face_engine)
    ranked = rank_combined_results(
        face_photo_ids=face_photo_ids,
        bib_photo_ids=bib_photo_ids,
        limit=limit,
    )
    return normalized_number, ranked, len(face_photo_ids), len(bib_photo_ids)


def combined_results_cache_key(session_uuid) -> str:
    return f"mapache:combined-search:{session_uuid}"


def create_combined_search_session(
    *, gallery: Gallery, normalized_number: str
) -> CombinedSearchSession:
    return CombinedSearchSession.objects.create(
        gallery=gallery,
        normalized_number=normalized_number,
        consent_version=settings.MAPACHE_FACE_CONSENT_VERSION,
        consented_at=timezone.now(),
        status=CombinedSearchSession.Status.PROCESSING,
        expires_at=CombinedSearchSession.new_expiration(),
    )


def complete_combined_search_session(
    session: CombinedSearchSession,
    results: list[CombinedSearchResult],
    *,
    face_results_count: int,
    bib_results_count: int,
) -> None:
    photo_ids = [result.photo_id for result in results]
    ttl = max(int((session.expires_at - timezone.now()).total_seconds()), 1)
    cache.set(combined_results_cache_key(session.uuid), photo_ids, timeout=ttl)
    session.status = CombinedSearchSession.Status.COMPLETED
    session.results_count = len(results)
    session.face_results_count = face_results_count
    session.bib_results_count = bib_results_count
    session.agreement_results_count = sum(
        result.matched_face and result.matched_bib for result in results
    )
    session.save(
        update_fields=[
            "status",
            "results_count",
            "face_results_count",
            "bib_results_count",
            "agreement_results_count",
        ]
    )


def check_combined_search_rate_limit(identifier: str) -> bool:
    key = f"mapache:combined-rate:{identifier}"
    if cache.add(key, 1, timeout=settings.MAPACHE_COMBINED_SEARCH_RATE_WINDOW):
        return True
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=settings.MAPACHE_COMBINED_SEARCH_RATE_WINDOW)
        return True
    return count <= settings.MAPACHE_COMBINED_SEARCH_RATE_LIMIT
