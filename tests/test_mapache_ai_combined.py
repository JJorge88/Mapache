from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.galleries.models import Gallery, Photo
from apps.galleries.services import publish_gallery
from apps.mapache_ai.combined.services import (
    CombinedSearchResult,
    check_combined_search_rate_limit,
    combined_results_cache_key,
    rank_combined_results,
    search_combined_in_gallery,
)
from apps.mapache_ai.engines.fake import FakeFaceEngine
from apps.mapache_ai.models import (
    CombinedSearchSession,
    DetectedBib,
    FaceEmbedding,
    GalleryAISettings,
)
from tests.factories import make_gallery, make_photo, make_user
from tests.image_helpers import make_test_image

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def combined_test_settings(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.MAPACHE_AI_ENABLED = True
    settings.MAPACHE_FACE_ENGINE = "apps.mapache_ai.engines.fake.FakeFaceEngine"
    settings.MAPACHE_FACE_MATCH_THRESHOLD = 0.7
    settings.MAPACHE_COMBINED_FACE_WEIGHT = 0.5
    settings.MAPACHE_COMBINED_BIB_WEIGHT = 0.5
    settings.MAPACHE_COMBINED_RRF_K = 60
    settings.MAPACHE_COMBINED_SEARCH_LIMIT = 100
    settings.MAPACHE_COMBINED_SEARCH_RATE_LIMIT = 10
    settings.MAPACHE_COMBINED_SEARCH_RATE_WINDOW = 600
    cache.clear()


def vector(x=1.0, y=0.0):
    return [x, y] + [0.0] * 126


def ready_photo(gallery, user, name):
    photo = make_photo(gallery, user, name)
    photo.optimized_file.save(f"{photo.uuid}.webp", ContentFile(b"optimized"), save=False)
    photo.thumbnail_file.save(f"{photo.uuid}.webp", ContentFile(b"thumbnail"), save=False)
    photo.processing_status = Photo.ProcessingStatus.READY
    photo.processed_at = timezone.now()
    Photo.objects.filter(pk=photo.pk).update(
        optimized_file=photo.optimized_file.name,
        thumbnail_file=photo.thumbnail_file.name,
        processing_status=photo.processing_status,
        processed_at=photo.processed_at,
    )
    photo.refresh_from_db()
    return photo


def enable_combined(gallery):
    return GalleryAISettings.objects.create(
        gallery=gallery,
        enabled=True,
        face_search_enabled=True,
        bib_search_enabled=True,
        face_indexing_status=GalleryAISettings.IndexingStatus.READY,
        bib_indexing_status=GalleryAISettings.IndexingStatus.READY,
    )


def add_face(photo, value=None):
    return FaceEmbedding.objects.create(
        gallery=photo.gallery,
        photo=photo,
        face_index=0,
        embedding=value or vector(),
        confidence=0.99,
        bounding_box={"x": 0, "y": 0, "width": 1, "height": 1},
    )


def add_bib(photo, number="247"):
    return DetectedBib.objects.create(
        gallery=photo.gallery,
        photo=photo,
        raw_text=number,
        normalized_number=number,
        confidence=0.95,
        bounding_box={"x": 0.2, "y": 0.2, "width": 0.3, "height": 0.2},
    )


def test_rrf_uses_union_and_promotes_signal_agreement():
    results = rank_combined_results(face_photo_ids=[1, 2], bib_photo_ids=[1, 3], rrf_k=60, limit=10)
    assert [result.photo_id for result in results] == [1, 2, 3]
    assert results[0].matched_face and results[0].matched_bib
    assert results[1].matched_face and not results[1].matched_bib
    assert results[2].matched_bib and not results[2].matched_face
    assert results[0].combined_score > results[1].combined_score


def test_rrf_normalizes_weights_limits_and_validates_configuration():
    results = rank_combined_results(
        face_photo_ids=[1, 2],
        bib_photo_ids=[3, 4],
        face_weight=3,
        bib_weight=1,
        rrf_k=10,
        limit=2,
    )
    assert [result.photo_id for result in results] == [1, 2]
    with pytest.raises(ValidationError):
        rank_combined_results(face_photo_ids=[1], bib_photo_ids=[], face_weight=0, bib_weight=0)
    with pytest.raises(ValidationError):
        rank_combined_results(face_photo_ids=[1], bib_photo_ids=[], rrf_k=0)


def test_combined_service_consumes_existing_search_apis():
    user = make_user()
    gallery = make_gallery(user)
    enable_combined(gallery)
    with patch(
        "apps.mapache_ai.combined.services.search_bibs_in_gallery",
        return_value=("247", [1, 3]),
    ) as bib_search:
        with patch(
            "apps.mapache_ai.combined.services.run_face_query", return_value=[1, 2]
        ) as face_search:
            normalized, results, face_count, bib_count = search_combined_in_gallery(
                gallery=gallery,
                image_bytes=b"query",
                query_number="247",
                face_engine=FakeFaceEngine(),
            )
    assert normalized == "247"
    assert [result.photo_id for result in results] == [1, 2, 3]
    assert (face_count, bib_count) == (2, 2)
    bib_search.assert_called_once_with(gallery=gallery, query_number="247")
    face_search.assert_called_once()


def test_real_combined_query_keeps_face_only_bib_only_and_never_crosses_gallery():
    user = make_user()
    gallery = make_gallery(user, title="A")
    foreign_gallery = make_gallery(user, title="B")
    enable_combined(gallery)
    enable_combined(foreign_gallery)
    both = ready_photo(gallery, user, "both.jpg")
    face_only = ready_photo(gallery, user, "face.jpg")
    bib_only = ready_photo(gallery, user, "bib.jpg")
    foreign = ready_photo(foreign_gallery, user, "foreign.jpg")
    add_face(both)
    add_bib(both)
    add_face(face_only, vector(0.95, 0.05))
    add_face(bib_only, vector(0.0, 1.0))
    add_bib(bib_only)
    add_face(foreign)
    add_bib(foreign)

    normalized, results, face_count, bib_count = search_combined_in_gallery(
        gallery=gallery,
        image_bytes=b"query",
        query_number="247",
        face_engine=FakeFaceEngine(),
    )

    assert normalized == "247"
    assert results[0].photo_id == both.id
    assert {result.photo_id for result in results} == {both.id, face_only.id, bib_only.id}
    assert foreign.id not in {result.photo_id for result in results}
    assert (face_count, bib_count) == (2, 2)


@pytest.mark.parametrize(
    ("face_enabled", "bib_enabled"), [(False, True), (True, False), (False, False)]
)
def test_combined_search_requires_both_features(face_enabled, bib_enabled):
    user = make_user()
    gallery = make_gallery(user)
    GalleryAISettings.objects.create(
        gallery=gallery,
        enabled=True,
        face_search_enabled=face_enabled,
        bib_search_enabled=bib_enabled,
    )
    with pytest.raises(ValidationError):
        search_combined_in_gallery(
            gallery=gallery,
            image_bytes=b"query",
            query_number="247",
            face_engine=FakeFaceEngine(),
        )


def test_public_combined_flow_requires_consent_and_never_stores_selfie(client, settings):
    user = make_user()
    gallery = publish_gallery(gallery=make_gallery(user), published_by=user)
    gallery.allow_photo_download = True
    gallery.save(update_fields=["allow_photo_download", "updated_at"])
    enable_combined(gallery)
    photo = ready_photo(gallery, user, "match.jpg")
    add_face(photo)
    add_bib(photo)
    url = reverse("mapache_ai_public:find_me_combined", args=[gallery.slug])
    files_before = {
        path.relative_to(settings.MEDIA_ROOT) for path in settings.MEDIA_ROOT.rglob("*")
    }

    missing_consent = client.post(
        url, {"query_number": "247", "query_image": make_test_image("query.jpg")}
    )
    assert missing_consent.status_code == 200
    assert CombinedSearchSession.objects.count() == 0

    response = client.post(
        url,
        {
            "query_number": "247",
            "query_image": make_test_image("query.jpg"),
            "consent": "on",
        },
    )
    assert response.status_code == 302
    session = CombinedSearchSession.objects.get()
    assert session.status == CombinedSearchSession.Status.COMPLETED
    assert session.results_count == 1
    assert session.face_results_count == 1
    assert session.bib_results_count == 1
    assert session.agreement_results_count == 1
    assert session.consent_version == settings.MAPACHE_FACE_CONSENT_VERSION
    files_after = {path.relative_to(settings.MEDIA_ROOT) for path in settings.MEDIA_ROOT.rglob("*")}
    assert files_after == files_before
    results = client.get(response.url)
    assert reverse("core_media:local_photo", args=[photo.uuid, "thumbnail"]) in (
        results.content.decode()
    )
    assert (
        reverse("galleries_public:photo_download", args=[gallery.slug, photo.uuid])
        in results.content.decode()
    )
    assert "combined_score" not in results.content.decode()


def test_hub_hides_combined_option_while_endpoint_remains_compatible(client):
    user = make_user()
    gallery = publish_gallery(gallery=make_gallery(user), published_by=user)
    settings_record = enable_combined(gallery)
    hub = client.get(reverse("mapache_ai_public:find_me", args=[gallery.slug]))
    combined_url = reverse("mapache_ai_public:find_me_combined", args=[gallery.slug])
    assert combined_url not in hub.content.decode()
    assert "FOTO + NÚMERO" not in hub.content.decode()
    assert client.get(combined_url).status_code == 200

    settings_record.bib_search_enabled = False
    settings_record.save(update_fields=["bib_search_enabled"])
    assert (
        client.get(combined_url).status_code
        == 404
    )


def test_dashboard_status_reports_derived_combined_availability(client):
    user = make_user()
    gallery = make_gallery(user)
    settings_record = enable_combined(gallery)
    client.force_login(user)
    url = reverse("mapache_ai_dashboard:status", args=[gallery.uuid])
    assert client.get(url).json()["combined_search_enabled"] is True
    settings_record.face_search_enabled = False
    settings_record.save(update_fields=["face_search_enabled"])
    assert client.get(url).json()["combined_search_enabled"] is False


def test_combined_private_pin_and_cross_gallery_session_are_protected(client):
    user = make_user()
    private = make_gallery(user, title="Private", visibility=Gallery.Visibility.PRIVATE_PIN)
    private.set_pin("4821")
    private.save(update_fields=["pin_hash"])
    publish_gallery(gallery=private, published_by=user)
    enable_combined(private)
    other = publish_gallery(gallery=make_gallery(user, title="Other"), published_by=user)
    enable_combined(other)
    url = reverse("mapache_ai_public:find_me_combined", args=[private.slug])
    blocked = client.get(url)
    assert blocked.status_code == 302
    assert blocked.url == reverse("galleries_public:access", args=[private.slug])
    client.post(reverse("galleries_public:access", args=[private.slug]), {"pin": "4821"})
    assert client.get(url).status_code == 200

    session = CombinedSearchSession.objects.create(
        gallery=private,
        normalized_number="247",
        consent_version="1.0",
        consented_at=timezone.now(),
        status=CombinedSearchSession.Status.COMPLETED,
        expires_at=CombinedSearchSession.new_expiration(),
    )
    cache.set(combined_results_cache_key(session.uuid), [], 3600)
    foreign_url = reverse("mapache_ai_public:combined_results", args=[other.slug, session.uuid])
    assert client.get(foreign_url).status_code == 404


def test_combined_rate_limit_is_separate_and_configurable(settings):
    settings.MAPACHE_COMBINED_SEARCH_RATE_LIMIT = 2
    assert check_combined_search_rate_limit("visitor")
    assert check_combined_search_rate_limit("visitor")
    assert not check_combined_search_rate_limit("visitor")


def test_public_combined_endpoint_enforces_rate_limit(client, settings):
    user = make_user()
    gallery = publish_gallery(gallery=make_gallery(user), published_by=user)
    enable_combined(gallery)
    photo = ready_photo(gallery, user, "match.jpg")
    add_face(photo)
    add_bib(photo)
    settings.MAPACHE_COMBINED_SEARCH_RATE_LIMIT = 1
    url = reverse("mapache_ai_public:find_me_combined", args=[gallery.slug])
    payload = {
        "query_number": "247",
        "query_image": make_test_image("first.jpg"),
        "consent": "on",
    }
    assert client.post(url, payload).status_code == 302
    limited = client.post(
        url,
        {
            "query_number": "247",
            "query_image": make_test_image("second.jpg"),
            "consent": "on",
        },
    )
    assert limited.status_code == 429


def test_combined_expiration_and_cleanup_remove_session_and_cached_results():
    user = make_user()
    gallery = make_gallery(user)
    session = CombinedSearchSession.objects.create(
        gallery=gallery,
        normalized_number="247",
        consent_version="1.0",
        consented_at=timezone.now(),
        status=CombinedSearchSession.Status.COMPLETED,
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    cache.set(combined_results_cache_key(session.uuid), [1], 3600)
    call_command("cleanup_combined_search_sessions")
    assert not CombinedSearchSession.objects.filter(pk=session.pk).exists()
    assert cache.get(combined_results_cache_key(session.uuid)) is None


def test_combined_result_dataclass_does_not_require_model_changes():
    result = CombinedSearchResult(1, 0.1, 0.2, 0.15, True, True)
    assert result.photo_id == 1
    assert not hasattr(result, "identity")
