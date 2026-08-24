from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.galleries.models import Gallery, Photo
from apps.galleries.services import publish_gallery
from apps.mapache_ai.engines.fake import FakeFaceEngine
from apps.mapache_ai.exceptions import FaceEngineUnavailable
from apps.mapache_ai.models import (
    FaceEmbedding,
    FaceSearchSession,
    GalleryAISettings,
    PhotoFaceIndex,
)
from apps.mapache_ai.services import (
    check_search_rate_limit,
    complete_search_session,
    create_search_session,
    delete_gallery_face_index,
    index_photo_faces_now,
    results_cache_key,
    search_faces_in_gallery,
)
from apps.mapache_ai.tasks import index_gallery_faces
from tests.factories import make_gallery, make_photo, make_user
from tests.image_helpers import make_test_image

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def ai_test_settings(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.MAPACHE_AI_ENABLED = True
    settings.MAPACHE_FACE_ENGINE = "apps.mapache_ai.engines.fake.FakeFaceEngine"
    settings.MAPACHE_FACE_MATCH_THRESHOLD = 0.363
    settings.MAPACHE_FACE_STRONG_MATCH_THRESHOLD = 0.45
    settings.MAPACHE_FACE_SEARCH_RATE_LIMIT = 10
    settings.MAPACHE_FACE_SEARCH_RATE_WINDOW = 600
    cache.clear()


def vector(x=1.0, y=0.0):
    return [x, y] + [0.0] * 126


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


def enable_ai(gallery):
    return GalleryAISettings.objects.create(
        gallery=gallery,
        enabled=True,
        face_search_enabled=True,
        indexing_status=GalleryAISettings.IndexingStatus.READY,
    )


def add_embedding(photo, value=None, face_index=0):
    return FaceEmbedding.objects.create(
        gallery=photo.gallery,
        photo=photo,
        face_index=face_index,
        embedding=value or vector(),
        confidence=0.99,
        bounding_box={"x": 1, "y": 2, "width": 30, "height": 40},
    )


def test_ai_models_defaults_constraints_and_expiration():
    user = make_user()
    gallery = make_gallery(user)
    ai_settings = GalleryAISettings.objects.create(gallery=gallery)
    photo = ready_photo(gallery, user)
    add_embedding(photo)
    session = create_search_session(gallery=gallery)

    assert ai_settings.enabled is False
    assert ai_settings.face_search_enabled is False
    assert ai_settings.indexing_status == GalleryAISettings.IndexingStatus.DISABLED
    assert session.consent_version == "1.0"
    assert session.consented_at is not None
    assert session.expires_at > timezone.now()
    assert not session.is_expired
    with pytest.raises(IntegrityError):
        add_embedding(photo)


def test_ready_photo_indexes_multiple_faces_and_reindex_is_idempotent():
    user = make_user()
    gallery = make_gallery(user)
    enable_ai(gallery)
    photo = ready_photo(gallery, user)
    engine = FakeFaceEngine(faces=[vector(), vector(0.0, 1.0)])

    assert index_photo_faces_now(photo.id, engine=engine) == 2
    assert index_photo_faces_now(photo.id, engine=engine) == 2

    assert FaceEmbedding.objects.filter(photo=photo).count() == 2
    state = PhotoFaceIndex.objects.get(photo=photo)
    assert state.status == PhotoFaceIndex.Status.READY
    assert state.face_count == 2


def test_failed_reindex_preserves_previous_embeddings():
    user = make_user()
    gallery = make_gallery(user)
    enable_ai(gallery)
    photo = ready_photo(gallery, user)
    previous = add_embedding(photo, vector())

    class BrokenEngine(FakeFaceEngine):
        def detect_faces(self, image_bytes):
            raise RuntimeError("provider details must not persist")

    with pytest.raises(RuntimeError):
        index_photo_faces_now(photo.id, engine=BrokenEngine())

    assert FaceEmbedding.objects.filter(pk=previous.pk).exists()
    state = PhotoFaceIndex.objects.get(photo=photo)
    assert state.status == PhotoFaceIndex.Status.ERROR
    assert "provider details" not in state.error


def test_ready_transition_schedules_ai_index_after_commit(
    django_capture_on_commit_callbacks,
):
    user = make_user()
    gallery = make_gallery(user)
    enable_ai(gallery)
    photo = make_photo(gallery, user)
    photo.optimized_file.save(f"{photo.uuid}.webp", ContentFile(b"optimized"), save=False)
    photo.processing_status = Photo.ProcessingStatus.READY
    photo.processed_at = timezone.now()

    with patch("apps.mapache_ai.tasks.index_photo_faces.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            photo.save(
                update_fields=[
                    "optimized_file",
                    "processing_status",
                    "processed_at",
                    "updated_at",
                ]
            )

    delay.assert_called_once_with(photo.pk)
    assert PhotoFaceIndex.objects.filter(photo=photo, status=PhotoFaceIndex.Status.PENDING).exists()


def test_gallery_task_only_schedules_individual_photo_tasks():
    user = make_user()
    gallery = make_gallery(user)
    enable_ai(gallery)
    first = ready_photo(gallery, user, "first.jpg")
    second = ready_photo(gallery, user, "second.jpg")

    with patch("apps.mapache_ai.tasks.index_photo_faces.delay") as delay:
        result = index_gallery_faces.run(gallery.pk)

    assert result == {"gallery_id": gallery.pk, "scheduled": 2}
    assert {call.args[0] for call in delay.call_args_list} == {first.pk, second.pk}


@pytest.mark.parametrize("status", [Photo.ProcessingStatus.PENDING, Photo.ProcessingStatus.ERROR])
def test_non_ready_photos_are_not_indexed(status):
    user = make_user()
    gallery = make_gallery(user)
    enable_ai(gallery)
    photo = make_photo(gallery, user)
    Photo.objects.filter(pk=photo.pk).update(processing_status=status)

    assert index_photo_faces_now(photo.id, engine=FakeFaceEngine()) == 0
    assert not FaceEmbedding.objects.filter(photo=photo).exists()


def test_zero_faces_is_successfully_indexed_without_embeddings():
    user = make_user()
    gallery = make_gallery(user)
    enable_ai(gallery)
    photo = ready_photo(gallery, user)
    engine = FakeFaceEngine(faces=[])

    assert index_photo_faces_now(photo.id, engine=engine) == 0
    state = PhotoFaceIndex.objects.get(photo=photo)
    assert state.status == PhotoFaceIndex.Status.READY
    assert state.face_count == 0


def test_deleting_or_reprocessing_photo_removes_embeddings():
    user = make_user()
    gallery = make_gallery(user)
    photo = ready_photo(gallery, user)
    add_embedding(photo)

    photo.processing_status = Photo.ProcessingStatus.PENDING
    photo.save(update_fields=["processing_status"])
    assert not FaceEmbedding.objects.filter(photo=photo).exists()

    photo.processing_status = Photo.ProcessingStatus.READY
    Photo.objects.filter(pk=photo.pk).update(processing_status=photo.processing_status)
    add_embedding(photo)
    photo.delete()
    assert not FaceEmbedding.objects.filter(photo_id=photo.id).exists()


def test_vector_search_ranks_deduplicates_limits_and_never_crosses_gallery():
    user = make_user()
    gallery_a = make_gallery(user, title="A")
    gallery_b = make_gallery(user, title="B")
    first = ready_photo(gallery_a, user, "first.jpg")
    second = ready_photo(gallery_a, user, "second.jpg")
    foreign = ready_photo(gallery_b, user, "foreign.jpg")
    add_embedding(first, vector(1.0, 0.0), 0)
    add_embedding(first, vector(0.99, 0.01), 1)
    add_embedding(second, vector(0.8, 0.2), 0)
    add_embedding(foreign, vector(1.0, 0.0), 0)

    results = search_faces_in_gallery(
        gallery=gallery_a, query_embedding=vector(), threshold=0.7, limit=2
    )

    assert results == [first.id, second.id]
    assert foreign.id not in results


def test_vector_search_respects_threshold_and_ready_state():
    user = make_user()
    gallery = make_gallery(user)
    matched = ready_photo(gallery, user, "matched.jpg")
    rejected = ready_photo(gallery, user, "rejected.jpg")
    add_embedding(matched, vector(1.0, 0.0))
    add_embedding(rejected, vector(0.0, 1.0))
    Photo.objects.filter(pk=matched.pk).update(processing_status=Photo.ProcessingStatus.ERROR)

    assert search_faces_in_gallery(gallery=gallery, query_embedding=vector()) == []
    with pytest.raises(ValidationError):
        search_faces_in_gallery(gallery=gallery, query_embedding=[1.0, 0.0])


def test_default_face_search_requires_a_strong_match_and_confirms_borderline_faces():
    user = make_user()
    gallery = make_gallery(user)
    strong = ready_photo(gallery, user, "strong.jpg")
    confirmed = ready_photo(gallery, user, "confirmed.jpg")
    unconfirmed = ready_photo(gallery, user, "unconfirmed.jpg")
    add_embedding(strong, vector(0.8, 0.6))
    add_embedding(confirmed, vector(0.4, 0.916515))
    add_embedding(unconfirmed, vector(0.4, -0.916515))

    results = search_faces_in_gallery(gallery=gallery, query_embedding=vector())

    assert results == [strong.id, confirmed.id]


def test_admin_can_enable_disable_reindex_delete_and_read_minimal_status(
    client, django_capture_on_commit_callbacks
):
    user = make_user()
    gallery = make_gallery(user)
    photo = ready_photo(gallery, user)
    add_embedding(photo)
    client.force_login(user)
    settings_url = reverse("mapache_ai_dashboard:settings", args=[gallery.uuid])

    with patch("apps.mapache_ai.tasks.index_gallery_faces.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(settings_url, {"enabled": "on", "face_search_enabled": "on"})
    assert response.status_code == 302
    ai_settings = GalleryAISettings.objects.get(gallery=gallery)
    assert ai_settings.enabled and ai_settings.face_search_enabled
    delay.assert_called_once_with(gallery.pk)
    assert AuditLog.objects.filter(action="MAPACHE_AI_ENABLED").exists()

    with patch("apps.mapache_ai.tasks.index_gallery_faces.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            assert (
                client.post(
                    reverse("mapache_ai_dashboard:reindex", args=[gallery.uuid])
                ).status_code
                == 302
            )
    delay.assert_called_once_with(gallery.pk)
    status = client.get(reverse("mapache_ai_dashboard:status", args=[gallery.uuid])).json()
    assert {
        "enabled",
        "face_search_enabled",
        "status",
        "status_label",
        "total_photos",
        "indexed_photos",
        "faces_detected",
        "pending_photos",
        "error_photos",
        "last_indexed_at",
    } <= set(status)
    assert {"face", "bib"} <= set(status)
    assert "embedding" not in str(status)

    delete_response = client.post(reverse("mapache_ai_dashboard:delete_index", args=[gallery.uuid]))
    assert delete_response.status_code == 302
    assert not FaceEmbedding.objects.filter(gallery=gallery).exists()
    assert AuditLog.objects.filter(action="GALLERY_FACE_INDEX_DELETED").exists()

    client.post(settings_url, {})
    ai_settings.refresh_from_db()
    assert ai_settings.enabled is False
    assert ai_settings.indexing_status == GalleryAISettings.IndexingStatus.DISABLED
    assert AuditLog.objects.filter(action="MAPACHE_AI_DISABLED").exists()


def test_admin_ai_mutations_and_status_require_authentication(client):
    user = make_user()
    gallery = make_gallery(user)
    urls = [
        ("get", reverse("mapache_ai_dashboard:settings", args=[gallery.uuid])),
        ("get", reverse("mapache_ai_dashboard:status", args=[gallery.uuid])),
        ("post", reverse("mapache_ai_dashboard:reindex", args=[gallery.uuid])),
        ("post", reverse("mapache_ai_dashboard:delete_index", args=[gallery.uuid])),
        ("post", reverse("mapache_ai_dashboard:reindex_bibs", args=[gallery.uuid])),
        ("post", reverse("mapache_ai_dashboard:delete_bib_index", args=[gallery.uuid])),
    ]
    for method, url in urls:
        response = getattr(client, method)(url)
        assert response.status_code == 302
        assert reverse("accounts:login") in response.url


def test_find_me_requires_consent_and_never_stores_selfie(client, settings):
    user = make_user()
    gallery = publish_gallery(gallery=make_gallery(user), published_by=user)
    enable_ai(gallery)
    photo = ready_photo(gallery, user)
    add_embedding(photo)
    url = reverse("mapache_ai_public:find_me", args=[gallery.slug])
    photo_count = Photo.objects.count()
    files_before = {
        path.relative_to(settings.MEDIA_ROOT) for path in settings.MEDIA_ROOT.rglob("*")
    }

    without_consent = client.post(url, {"query_image": make_test_image("selfie.jpg")})
    assert without_consent.status_code == 200
    assert FaceSearchSession.objects.count() == 0

    with_consent = client.post(
        url,
        {"query_image": make_test_image("selfie.jpg"), "consent": "on"},
    )
    session = FaceSearchSession.objects.get()
    assert with_consent.status_code == 302
    assert session.status == FaceSearchSession.Status.COMPLETED
    assert session.consent_version == "1.0"
    assert Photo.objects.count() == photo_count
    files_after = {path.relative_to(settings.MEDIA_ROOT) for path in settings.MEDIA_ROOT.rglob("*")}
    assert files_after == files_before
    assert "embedding" not in with_consent.content.decode().lower()


def test_find_me_offers_camera_and_shows_gallery_before_search(client):
    user = make_user()
    gallery = publish_gallery(gallery=make_gallery(user), published_by=user)
    enable_ai(gallery)
    photo = ready_photo(gallery, user)

    response = client.get(reverse("mapache_ai_public:find_me", args=[gallery.slug]))
    content = response.content.decode()

    assert response.status_code == 200
    assert "data-camera-source" in content
    assert "data-camera-input" in content
    assert "data-camera-modal" in content
    assert "data-camera-video" in content
    assert "data-camera-shutter" in content
    assert "data-camera-photo" in content
    assert "data-camera-retake" in content
    assert "data-camera-use" in content
    assert 'capture="user"' in content
    assert 'accept="image/*"' in content
    assert "data-library-source" in content
    assert 'accept="image/jpeg,image/png,image/webp"' in content
    assert "Todas las fotografías." in content
    assert reverse("core_media:local_photo", args=[photo.uuid, "thumbnail"]) in content


@pytest.mark.parametrize(
    ("engine", "message"),
    [
        (FakeFaceEngine(faces=[]), "No encontramos un rostro claro"),
        (
            FakeFaceEngine(faces=[vector(), vector(0.0, 1.0)]),
            "Vemos varias personas",
        ),
    ],
)
def test_query_requires_exactly_one_face(client, engine, message):
    user = make_user()
    gallery = publish_gallery(gallery=make_gallery(user), published_by=user)
    enable_ai(gallery)
    url = reverse("mapache_ai_public:find_me", args=[gallery.slug])

    with patch("apps.mapache_ai.views.get_face_engine", return_value=engine):
        response = client.post(
            url,
            {"query_image": make_test_image("query.jpg"), "consent": "on"},
        )
    assert response.status_code == 200
    assert message in response.content.decode()


def test_engine_failure_keeps_normal_gallery_available(client):
    user = make_user()
    gallery = publish_gallery(gallery=make_gallery(user), published_by=user)
    enable_ai(gallery)
    url = reverse("mapache_ai_public:find_me", args=[gallery.slug])

    with patch(
        "apps.mapache_ai.views.get_face_engine",
        side_effect=FaceEngineUnavailable("model unavailable"),
    ):
        response = client.post(
            url,
            {"query_image": make_test_image("query.jpg"), "consent": "on"},
        )

    assert response.status_code == 200
    assert "Esta búsqueda no está disponible" in response.content.decode()
    assert client.get(reverse("galleries_public:detail", args=[gallery.slug])).status_code == 200


def test_query_rejects_corrupt_and_oversized_images(client, settings):
    user = make_user()
    gallery = publish_gallery(gallery=make_gallery(user), published_by=user)
    enable_ai(gallery)
    settings.MAPACHE_FACE_QUERY_MAX_MB = 0
    url = reverse("mapache_ai_public:find_me", args=[gallery.slug])

    corrupt = client.post(
        url,
        {
            "query_image": SimpleUploadedFile("bad.jpg", b"broken", content_type="image/jpeg"),
            "consent": "on",
        },
    )
    assert corrupt.status_code == 200
    assert FaceSearchSession.objects.count() == 0

    oversized = client.post(
        url,
        {"query_image": make_test_image("large.jpg"), "consent": "on"},
    )
    assert "límite de 0 MB" in oversized.content.decode()


def test_private_gallery_find_me_respects_pin_access(client):
    user = make_user()
    gallery = make_gallery(user, visibility=Gallery.Visibility.PRIVATE_PIN)
    gallery.set_pin("4821")
    gallery.save(update_fields=["pin_hash"])
    publish_gallery(gallery=gallery, published_by=user)
    enable_ai(gallery)
    url = reverse("mapache_ai_public:find_me", args=[gallery.slug])

    blocked = client.get(url)
    assert blocked.status_code == 302
    assert blocked.url == reverse("galleries_public:access", args=[gallery.slug])

    client.post(reverse("galleries_public:access", args=[gallery.slug]), {"pin": "4821"})
    assert client.get(url).status_code == 200


def test_global_or_gallery_flags_disable_public_search(client, settings):
    user = make_user()
    gallery = publish_gallery(gallery=make_gallery(user), published_by=user)
    ai_settings = enable_ai(gallery)
    url = reverse("mapache_ai_public:find_me", args=[gallery.slug])

    settings.MAPACHE_AI_ENABLED = False
    assert client.get(url).status_code == 404
    settings.MAPACHE_AI_ENABLED = True
    ai_settings.face_search_enabled = False
    ai_settings.save(update_fields=["face_search_enabled"])
    assert client.get(url).status_code == 404


def test_results_are_gallery_bound_ready_only_scoreless_and_expire(client):
    user = make_user()
    gallery_a = publish_gallery(gallery=make_gallery(user, title="A"), published_by=user)
    gallery_a.allow_photo_download = True
    gallery_a.save(update_fields=["allow_photo_download", "updated_at"])
    gallery_b = publish_gallery(gallery=make_gallery(user, title="B"), published_by=user)
    enable_ai(gallery_a)
    enable_ai(gallery_b)
    ready = ready_photo(gallery_a, user, "ready.jpg")
    error = ready_photo(gallery_a, user, "error.jpg")
    Photo.objects.filter(pk=error.pk).update(processing_status=Photo.ProcessingStatus.ERROR)
    session = create_search_session(gallery=gallery_a)
    complete_search_session(session, [ready.id, error.id])

    own_url = reverse("mapache_ai_public:results", args=[gallery_a.slug, session.uuid])
    response = client.get(own_url)
    content = response.content.decode()
    assert response.status_code == 200
    assert reverse("core_media:local_photo", args=[ready.uuid, "thumbnail"]) in content
    assert reverse("galleries_public:photo_download", args=[gallery_a.slug, ready.uuid]) in content
    assert reverse("core_media:local_photo", args=[error.uuid, "thumbnail"]) not in content
    assert "%" not in content

    foreign_url = reverse("mapache_ai_public:results", args=[gallery_b.slug, session.uuid])
    assert client.get(foreign_url).status_code == 404
    FaceSearchSession.objects.filter(pk=session.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert client.get(own_url).status_code == 404


def test_rate_limit_and_cleanup_command(client, settings):
    user = make_user()
    gallery = publish_gallery(gallery=make_gallery(user), published_by=user)
    enable_ai(gallery)
    settings.MAPACHE_FACE_SEARCH_RATE_LIMIT = 1
    url = reverse("mapache_ai_public:find_me", args=[gallery.slug])
    payload = {"query_image": make_test_image("one.jpg"), "consent": "on"}
    assert client.post(url, payload).status_code == 302
    limited = client.post(url, {"query_image": make_test_image("two.jpg"), "consent": "on"})
    assert limited.status_code == 429

    session = FaceSearchSession.objects.first()
    FaceSearchSession.objects.filter(pk=session.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    call_command("cleanup_face_search_sessions")
    assert not FaceSearchSession.objects.filter(pk=session.pk).exists()
    assert cache.get(results_cache_key(session.uuid)) is None


def test_rate_limiter_has_configurable_window(settings):
    settings.MAPACHE_FACE_SEARCH_RATE_LIMIT = 2
    assert check_search_rate_limit("visitor")
    assert check_search_rate_limit("visitor")
    assert not check_search_rate_limit("visitor")


def test_delete_index_is_explicit_and_does_not_delete_photos():
    user = make_user()
    gallery = make_gallery(user)
    ai_settings = enable_ai(gallery)
    photo = ready_photo(gallery, user)
    add_embedding(photo)

    assert delete_gallery_face_index(gallery=gallery, deleted_by=user) == 1
    ai_settings.refresh_from_db()
    assert Photo.objects.filter(pk=photo.pk).exists()
    assert ai_settings.enabled is True
    assert ai_settings.indexing_status == GalleryAISettings.IndexingStatus.PENDING


def test_embedding_never_appears_in_dashboard_or_public_html(client):
    user = make_user()
    gallery = publish_gallery(gallery=make_gallery(user), published_by=user)
    enable_ai(gallery)
    photo = ready_photo(gallery, user)
    add_embedding(photo, vector(0.123456789, 0.987654321))
    client.force_login(user)

    dashboard = client.get(reverse("mapache_ai_dashboard:settings", args=[gallery.uuid]))
    public = client.get(reverse("mapache_ai_public:find_me", args=[gallery.slug]))
    combined = dashboard.content.decode() + public.content.decode()
    assert "0.123456789" not in combined
    assert "0.987654321" not in combined
    assert "embedding" not in str(AuditLog.objects.values_list("metadata", flat=True))
