from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.galleries.models import Gallery, Photo
from apps.galleries.services import publish_gallery
from apps.mapache_ai.bib.base import RecognizedBib
from apps.mapache_ai.bib.fake import FakeBibRecognitionEngine
from apps.mapache_ai.bib.normalization import normalize_bib_text
from apps.mapache_ai.bib.paddleocr import recognized_bibs_from_payload
from apps.mapache_ai.bib.services import (
    check_bib_search_rate_limit,
    delete_gallery_bib_index,
    index_photo_bibs_now,
    search_bibs_in_gallery,
)
from apps.mapache_ai.bib.tasks import index_gallery_bibs
from apps.mapache_ai.models import BibPhotoAnalysis, DetectedBib, GalleryAISettings
from tests.factories import make_gallery, make_photo, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def bib_test_settings(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.MAPACHE_AI_ENABLED = True
    settings.MAPACHE_BIB_ENGINE = "apps.mapache_ai.bib.fake.FakeBibRecognitionEngine"
    settings.MAPACHE_BIB_MIN_CONFIDENCE = 0.60
    settings.MAPACHE_BIB_SEARCH_RATE_LIMIT = 30
    settings.MAPACHE_BIB_SEARCH_RATE_WINDOW = 600
    cache.clear()


def ready_photo(gallery, user, name="ready.jpg"):
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


def enable_bibs(gallery, *, bib_format=GalleryAISettings.BibFormat.NUMERIC):
    return GalleryAISettings.objects.create(
        gallery=gallery,
        enabled=True,
        bib_search_enabled=True,
        bib_format=bib_format,
        bib_indexing_status=GalleryAISettings.IndexingStatus.READY,
    )


def add_bib(photo, number="247", confidence=0.9):
    return DetectedBib.objects.create(
        gallery=photo.gallery,
        photo=photo,
        raw_text=number,
        normalized_number=number,
        confidence=confidence,
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.2, "height": 0.1},
    )


@pytest.mark.parametrize(
    ("raw", "bib_format", "expected"),
    [
        ("247", "NUMERIC", "247"),
        (" 247 ", "NUMERIC", "247"),
        ("0042", "NUMERIC", "0042"),
        ("O247", "NUMERIC", "0247"),
        ("A12", "ALPHANUMERIC", "A12"),
        ("B12", "ALPHANUMERIC", "B12"),
        ("A12", "NUMERIC", None),
        ("12@", "ALPHANUMERIC", None),
    ],
)
def test_normalization_is_format_aware(raw, bib_format, expected):
    assert normalize_bib_text(raw, bib_format=bib_format) == expected


def test_normalization_enforces_length_without_losing_leading_zeroes():
    assert normalize_bib_text("0042", bib_format="NUMERIC", min_length=4, max_length=4) == "0042"
    assert normalize_bib_text("42", bib_format="NUMERIC", min_length=4, max_length=6) is None


def test_paddleocr_payload_preserves_confidence_and_normalizes_geometry():
    detections = recognized_bibs_from_payload(
        {
            "res": {
                "rec_texts": ["017", "Texto", ""],
                "rec_scores": [0.997, 0.8, 0.9],
                "rec_boxes": [[100, 50, 300, 150], [900, 450, 1100, 550], [0, 0, 1, 1]],
            }
        },
        image_width=1000,
        image_height=500,
    )

    assert [item.raw_text for item in detections] == ["017", "Texto"]
    assert detections[0].confidence == pytest.approx(0.997)
    assert detections[0].bounding_box == {
        "x": 0.1,
        "y": 0.1,
        "width": 0.2,
        "height": 0.2,
    }
    assert detections[1].bounding_box["width"] == pytest.approx(0.1)


def test_bib_models_defaults_constraints_and_gallery_validation():
    user = make_user()
    gallery = make_gallery(user)
    foreign_gallery = make_gallery(user, title="Foreign")
    photo = ready_photo(gallery, user)
    ai_settings = GalleryAISettings.objects.create(gallery=gallery)

    assert ai_settings.bib_search_enabled is False
    assert ai_settings.bib_format == GalleryAISettings.BibFormat.NUMERIC
    assert ai_settings.bib_indexing_status == GalleryAISettings.IndexingStatus.DISABLED
    invalid = DetectedBib(
        gallery=foreign_gallery,
        photo=photo,
        raw_text="247",
        normalized_number="247",
        confidence=0.8,
        bounding_box={},
    )
    with pytest.raises(ValidationError):
        invalid.full_clean()
    with pytest.raises(IntegrityError), transaction.atomic():
        DetectedBib.objects.create(
            gallery=gallery,
            photo=photo,
            raw_text="247",
            normalized_number="247",
            confidence=1.2,
            bounding_box={},
        )


def test_ready_photo_indexes_zero_one_multiple_and_is_idempotent():
    user = make_user()
    gallery = make_gallery(user)
    enable_bibs(gallery)
    photo = ready_photo(gallery, user)
    zero = FakeBibRecognitionEngine(detections=[])
    assert index_photo_bibs_now(photo.id, engine=zero) == 0
    assert BibPhotoAnalysis.objects.get(photo=photo).status == BibPhotoAnalysis.Status.READY

    detections = [
        RecognizedBib("247", 0.95, {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}),
        RecognizedBib("042", 0.90, {"x": 0.6, "y": 0.1, "width": 0.2, "height": 0.2}),
    ]
    engine = FakeBibRecognitionEngine(detections=detections)
    assert index_photo_bibs_now(photo.id, engine=engine) == 2
    assert index_photo_bibs_now(photo.id, engine=engine) == 2
    numbers = set(
        DetectedBib.objects.filter(photo=photo).values_list("normalized_number", flat=True)
    )
    assert numbers == {
        "247",
        "042",
    }


def test_index_deduplicates_overlapping_result_and_filters_confidence():
    user = make_user()
    gallery = make_gallery(user)
    enable_bibs(gallery)
    photo = ready_photo(gallery, user)
    box = {"x": 0.1, "y": 0.1, "width": 0.3, "height": 0.3}
    engine = FakeBibRecognitionEngine(
        detections=[
            RecognizedBib("247", 0.95, box),
            RecognizedBib("247", 0.90, box),
            RecognizedBib("999", 0.20, {"x": 0.7, "y": 0.1, "width": 0.2, "height": 0.2}),
        ]
    )
    assert index_photo_bibs_now(photo.id, engine=engine) == 1
    assert DetectedBib.objects.get(photo=photo).normalized_number == "247"


def test_failed_reindex_preserves_previous_bibs_and_sanitizes_error():
    user = make_user()
    gallery = make_gallery(user)
    enable_bibs(gallery)
    photo = ready_photo(gallery, user)
    previous = add_bib(photo)

    class BrokenEngine(FakeBibRecognitionEngine):
        def recognize_bibs(self, image_bytes, *, bib_format):
            raise RuntimeError("secret provider details")

    with pytest.raises(RuntimeError):
        index_photo_bibs_now(photo.id, engine=BrokenEngine())
    assert DetectedBib.objects.filter(pk=previous.pk).exists()
    analysis = BibPhotoAnalysis.objects.get(photo=photo)
    assert analysis.status == BibPhotoAnalysis.Status.ERROR
    assert "secret" not in analysis.error


@pytest.mark.parametrize("status", [Photo.ProcessingStatus.PENDING, Photo.ProcessingStatus.ERROR])
def test_non_ready_photos_are_not_indexed(status):
    user = make_user()
    gallery = make_gallery(user)
    enable_bibs(gallery)
    photo = make_photo(gallery, user)
    Photo.objects.filter(pk=photo.pk).update(processing_status=status)
    assert index_photo_bibs_now(photo.id, engine=FakeBibRecognitionEngine()) == 0


def test_gallery_task_schedules_individual_tasks_only():
    user = make_user()
    gallery = make_gallery(user)
    enable_bibs(gallery)
    first = ready_photo(gallery, user, "first.jpg")
    second = ready_photo(gallery, user, "second.jpg")
    with patch("apps.mapache_ai.bib.tasks.index_photo_bibs.delay") as delay:
        result = index_gallery_bibs.run(gallery.pk)
    assert result == {"gallery_id": gallery.pk, "scheduled": 2}
    assert {call.args[0] for call in delay.call_args_list} == {first.pk, second.pk}


def test_ready_transition_schedules_face_and_bib_independently(django_capture_on_commit_callbacks):
    user = make_user()
    gallery = make_gallery(user)
    enable_bibs(gallery)
    photo = make_photo(gallery, user)
    photo.optimized_file.save(f"{photo.uuid}.webp", ContentFile(b"optimized"), save=False)
    photo.processing_status = Photo.ProcessingStatus.READY
    photo.processed_at = timezone.now()
    with patch("apps.mapache_ai.bib.tasks.index_photo_bibs.delay") as bib_delay:
        with patch("apps.mapache_ai.tasks.index_photo_faces.delay") as face_delay:
            with django_capture_on_commit_callbacks(execute=True):
                photo.save(
                    update_fields=[
                        "optimized_file",
                        "processing_status",
                        "processed_at",
                        "updated_at",
                    ]
                )
    bib_delay.assert_called_once_with(photo.pk)
    face_delay.assert_not_called()


def test_search_is_exact_deduplicated_ready_only_and_cross_gallery_safe():
    user = make_user()
    gallery_a = make_gallery(user, title="A")
    gallery_b = make_gallery(user, title="B")
    enable_bibs(gallery_a)
    enable_bibs(gallery_b)
    own = ready_photo(gallery_a, user, "own.jpg")
    foreign = ready_photo(gallery_b, user, "foreign.jpg")
    not_ready = ready_photo(gallery_a, user, "error.jpg")
    add_bib(own, "247", 0.8)
    add_bib(own, "247", 0.95)
    add_bib(foreign, "247", 0.99)
    add_bib(not_ready, "247", 0.99)
    Photo.objects.filter(pk=not_ready.pk).update(processing_status=Photo.ProcessingStatus.ERROR)

    normalized, results = search_bibs_in_gallery(gallery=gallery_a, query_number=" 247 ")
    assert normalized == "247"
    assert results == [own.id]
    assert foreign.id not in results
    assert search_bibs_in_gallery(gallery=gallery_a, query_number="248")[1] == []


def test_search_preserves_leading_zero_and_allows_alphanumeric():
    user = make_user()
    numeric_gallery = make_gallery(user, title="Numeric")
    alpha_gallery = make_gallery(user, title="Alpha")
    enable_bibs(numeric_gallery)
    enable_bibs(alpha_gallery, bib_format=GalleryAISettings.BibFormat.ALPHANUMERIC)
    zero = ready_photo(numeric_gallery, user, "zero.jpg")
    alpha = ready_photo(alpha_gallery, user, "alpha.jpg")
    add_bib(zero, "0042")
    add_bib(alpha, "A12")
    assert search_bibs_in_gallery(gallery=numeric_gallery, query_number="0042")[1] == [zero.id]
    assert search_bibs_in_gallery(gallery=alpha_gallery, query_number="a12")[1] == [alpha.id]
    with pytest.raises(ValidationError):
        search_bibs_in_gallery(gallery=numeric_gallery, query_number="A12")


def test_numeric_search_treats_printed_leading_zeroes_as_the_same_bib():
    user = make_user()
    gallery = make_gallery(user, title="Padded bibs")
    enable_bibs(gallery)
    exact = ready_photo(gallery, user, "17.jpg")
    padded = ready_photo(gallery, user, "017.jpg")
    add_bib(exact, "17", confidence=0.8)
    add_bib(padded, "017", confidence=0.99)

    assert search_bibs_in_gallery(gallery=gallery, query_number="17")[1] == [exact.id, padded.id]
    assert search_bibs_in_gallery(gallery=gallery, query_number="017")[1] == [padded.id, exact.id]


def test_single_digit_search_includes_containing_numbers_with_exact_matches_first():
    user = make_user()
    gallery = make_gallery(user, title="Single digit")
    enable_bibs(gallery)
    exact = ready_photo(gallery, user, "exact.jpg")
    containing = ready_photo(gallery, user, "containing.jpg")
    unrelated = ready_photo(gallery, user, "unrelated.jpg")
    add_bib(exact, "1", confidence=0.70)
    add_bib(containing, "217", confidence=0.99)
    add_bib(unrelated, "247", confidence=1.0)

    normalized, results = search_bibs_in_gallery(gallery=gallery, query_number="1")

    assert normalized == "1"
    assert results == [exact.id, containing.id]


def test_multi_digit_search_remains_exact():
    user = make_user()
    gallery = make_gallery(user, title="Exact multi digit")
    enable_bibs(gallery)
    exact = ready_photo(gallery, user, "exact-24.jpg")
    containing = ready_photo(gallery, user, "containing-247.jpg")
    add_bib(exact, "24")
    add_bib(containing, "247", confidence=1.0)

    assert search_bibs_in_gallery(gallery=gallery, query_number="24")[1] == [exact.id]


def test_dashboard_enable_reindex_delete_and_nested_status(
    client, django_capture_on_commit_callbacks
):
    user = make_user()
    gallery = make_gallery(user)
    photo = ready_photo(gallery, user)
    client.force_login(user)
    settings_url = reverse("mapache_ai_dashboard:settings", args=[gallery.uuid])
    payload = {
        "enabled": "on",
        "bib_search_enabled": "on",
        "bib_format": "NUMERIC",
        "bib_min_length": "1",
        "bib_max_length": "6",
    }
    with patch("apps.mapache_ai.bib.tasks.index_gallery_bibs.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            assert client.post(settings_url, payload).status_code == 302
    delay.assert_called_once_with(gallery.pk)
    ai_settings = GalleryAISettings.objects.get(gallery=gallery)
    assert ai_settings.bib_search_enabled
    assert AuditLog.objects.filter(action="MAPACHE_BIB_SEARCH_ENABLED").exists()

    with patch("apps.mapache_ai.bib.tasks.index_gallery_bibs.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            client.post(reverse("mapache_ai_dashboard:reindex_bibs", args=[gallery.uuid]))
    delay.assert_called_once_with(gallery.pk)
    assert AuditLog.objects.filter(action="MAPACHE_BIB_REINDEX_REQUESTED").exists()

    add_bib(photo)
    status = client.get(reverse("mapache_ai_dashboard:status", args=[gallery.uuid])).json()
    assert {"face", "bib"} <= set(status)
    assert status["bib"]["detected"] == 1
    assert "raw_text" not in str(status)
    delete_url = reverse("mapache_ai_dashboard:delete_bib_index", args=[gallery.uuid])
    assert client.post(delete_url).status_code == 302
    assert not DetectedBib.objects.filter(gallery=gallery).exists()
    assert AuditLog.objects.filter(action="GALLERY_BIB_INDEX_DELETED").exists()

    assert (
        client.get(reverse("mapache_ai_dashboard:reindex_bibs", args=[gallery.uuid])).status_code
        == 405
    )
    assert client.get(delete_url).status_code == 405
    assert client.post(settings_url, {}).status_code == 302
    ai_settings.refresh_from_db()
    assert ai_settings.bib_search_enabled is False
    assert ai_settings.bib_indexing_status == GalleryAISettings.IndexingStatus.DISABLED
    assert AuditLog.objects.filter(action="MAPACHE_BIB_SEARCH_DISABLED").exists()


def test_public_bib_flow_has_no_consent_and_results_are_gallery_bound(client):
    user = make_user()
    gallery = publish_gallery(gallery=make_gallery(user), published_by=user)
    gallery.allow_photo_download = True
    gallery.save(update_fields=["allow_photo_download", "updated_at"])
    enable_bibs(gallery)
    photo = ready_photo(gallery, user)
    add_bib(photo)
    hub = client.get(reverse("mapache_ai_public:find_me", args=[gallery.slug]))
    assert hub.status_code == 302
    assert hub.url == reverse("mapache_ai_public:find_me_number", args=[gallery.slug])
    number_page = client.get(hub.url)
    assert "¿Cuál es tu número?" in number_page.content.decode()
    assert "Encuentra<br>tus fotos." in number_page.content.decode()
    assert "Todas las fotografías." in number_page.content.decode()
    assert 'data-gallery-lightbox-root' in number_page.content.decode()
    assert "consent" not in number_page.content.decode().lower()
    response = client.post(
        reverse("mapache_ai_public:find_me_number", args=[gallery.slug]),
        {"query_number": "247"},
    )
    assert response.status_code == 302
    assert response.url.endswith("#fotos")
    results = client.get(response.url)
    assert results.status_code == 200
    assert "#247" in results.content.decode()
    assert "1 momento encontrado." in results.content.decode()
    assert "VER TODAS LAS FOTOGRAFÍAS" in results.content.decode()
    assert 'data-gallery-lightbox-root' in results.content.decode()
    assert '<button class="gallery-photo-open"' in results.content.decode()
    assert "REGRESAR" in results.content.decode()
    assert reverse("core_media:local_photo", args=[photo.uuid, "thumbnail"]) in (
        results.content.decode()
    )
    assert (
        reverse("galleries_public:photo_download", args=[gallery.slug, photo.uuid])
        in results.content.decode()
    )


def test_bib_disabled_endpoint_is_hidden_and_private_pin_is_respected(client):
    user = make_user()
    public = publish_gallery(gallery=make_gallery(user, title="Public"), published_by=user)
    GalleryAISettings.objects.create(gallery=public, enabled=True, face_search_enabled=True)
    number_url = reverse("mapache_ai_public:find_me_number", args=[public.slug])
    assert client.get(number_url).status_code == 404

    private = make_gallery(user, title="Private", visibility=Gallery.Visibility.PRIVATE_PIN)
    private.set_pin("4821")
    private.save(update_fields=["pin_hash"])
    publish_gallery(gallery=private, published_by=user)
    enable_bibs(private)
    url = reverse("mapache_ai_public:find_me_number", args=[private.slug])
    blocked = client.get(url)
    assert blocked.status_code == 302
    assert blocked.url == reverse("galleries_public:access", args=[private.slug])
    client.post(reverse("galleries_public:access", args=[private.slug]), {"pin": "4821"})
    assert client.get(url).status_code == 200


def test_bib_rate_limit_is_independent_and_configurable(settings):
    settings.MAPACHE_BIB_SEARCH_RATE_LIMIT = 2
    assert check_bib_search_rate_limit("visitor")
    assert check_bib_search_rate_limit("visitor")
    assert not check_bib_search_rate_limit("visitor")


def test_public_bib_rate_limit_and_unlisted_direct_access(client, settings):
    user = make_user()
    gallery = make_gallery(user, visibility=Gallery.Visibility.UNLISTED)
    publish_gallery(gallery=gallery, published_by=user)
    enable_bibs(gallery)
    settings.MAPACHE_BIB_SEARCH_RATE_LIMIT = 1
    url = reverse("mapache_ai_public:find_me_number", args=[gallery.slug])
    assert client.get(url).status_code == 200
    assert client.post(url, {"query_number": "247"}).status_code == 302
    assert client.post(url, {"query_number": "247"}).status_code == 429


def test_delete_bib_index_does_not_touch_photos_or_face_data():
    user = make_user()
    gallery = make_gallery(user)
    enable_bibs(gallery)
    photo = ready_photo(gallery, user)
    add_bib(photo)
    assert delete_gallery_bib_index(gallery=gallery, deleted_by=user) == 1
    assert Photo.objects.filter(pk=photo.pk).exists()
