import zipfile
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.galleries.downloads import (
    gallery_content_fingerprint,
    invalidate_gallery_downloads,
    prepare_gallery_download,
    request_gallery_download,
    session_authorization_hash,
    zip_member_name,
)
from apps.galleries.models import Gallery, GalleryDownload, Photo
from apps.galleries.services import add_photo, publish_gallery, reorder_photos
from apps.galleries.tasks import build_gallery_download
from tests.factories import make_gallery, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_path_storage(settings):
    settings.STORAGE_BACKEND = "local"
    settings.STORAGES = {
        "default": {"BACKEND": "tests.storage_backends.NoPathMemoryStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    settings.MAPACHE_GALLERY_DOWNLOAD_MAX_PHOTOS = 10000
    settings.MAPACHE_GALLERY_DOWNLOAD_MAX_BYTES = 0
    storage = default_storage
    storage.files.clear()
    return storage


def ready_photo(gallery, user, name="photo.jpg", content=b"original", order=0):
    photo = add_photo(
        gallery=gallery,
        original_file=SimpleUploadedFile(name, content, content_type="image/jpeg"),
        uploaded_by=user,
        mime_type="image/jpeg",
        file_size=len(content),
    )
    photo.processing_status = Photo.ProcessingStatus.READY
    photo.sort_order = order
    photo.save(update_fields=["processing_status", "sort_order", "updated_at"])
    return photo


def downloadable_gallery(user, **kwargs):
    gallery = make_gallery(
        user,
        allow_photo_download=True,
        allow_gallery_download=True,
        **kwargs,
    )
    return publish_gallery(gallery=gallery, published_by=user)


def session_request(path="/"):
    request = RequestFactory().get(path)
    SessionMiddleware(lambda _request: None).process_request(request)
    request.session.save()
    return request


def pending_download(gallery, request):
    return GalleryDownload.objects.create(
        gallery=gallery,
        photo_count=gallery.photos.filter(processing_status=Photo.ProcessingStatus.READY).count(),
        content_fingerprint=gallery_content_fingerprint(gallery),
        authorization_hash=session_authorization_hash(request),
    )


def archive_bytes(download):
    with download.file.storage.open(download.file.name, "rb") as stored:
        return stored.read()


def test_individual_download_returns_original_with_safe_attachment(client):
    user = make_user()
    gallery = downloadable_gallery(user)
    photo = ready_photo(gallery, user, "camera.jpg", b"full-resolution")
    photo.original_filename = "../../unsafe\r\nX-Test: yes.jpg"
    photo.save(update_fields=["original_filename", "updated_at"])
    url = reverse("galleries_public:photo_download", args=[gallery.slug, photo.uuid])

    response = client.get(url)
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"full-resolution"
    disposition = response["Content-Disposition"]
    assert disposition.startswith("attachment;")
    assert ".." not in disposition
    assert "\r" not in disposition and "\n" not in disposition
    assert "X-Test" in disposition


@pytest.mark.parametrize("status", [Gallery.Status.DRAFT, Gallery.Status.ARCHIVED])
def test_individual_download_rejects_disabled_unpublished_and_cross_gallery(client, status):
    user = make_user()
    gallery = make_gallery(user, allow_photo_download=False)
    photo = ready_photo(gallery, user)
    url = reverse("galleries_public:photo_download", args=[gallery.slug, photo.uuid])
    assert client.get(url).status_code == 404

    gallery.allow_photo_download = True
    gallery.status = status
    gallery.save(update_fields=["allow_photo_download", "status", "updated_at"])
    assert client.get(url).status_code == 404

    other = downloadable_gallery(user, title=f"Other {status}")
    cross_url = reverse("galleries_public:photo_download", args=[other.slug, photo.uuid])
    assert client.get(cross_url).status_code == 404


def test_private_pin_is_required_for_individual_and_gallery_download(client):
    user = make_user()
    gallery = make_gallery(
        user,
        visibility=Gallery.Visibility.PRIVATE_PIN,
        allow_photo_download=True,
        allow_gallery_download=True,
    )
    gallery.set_pin("4821")
    gallery.save(update_fields=["pin_hash", "updated_at"])
    gallery = publish_gallery(gallery=gallery, published_by=user)
    photo = ready_photo(gallery, user)
    photo_url = reverse("galleries_public:photo_download", args=[gallery.slug, photo.uuid])
    gallery_url = reverse("galleries_public:download_request", args=[gallery.slug])
    assert client.get(photo_url).status_code == 404
    assert client.post(gallery_url).status_code == 404

    client.post(reverse("galleries_public:access", args=[gallery.slug]), {"pin": "4821"})
    assert client.get(photo_url).status_code == 200
    with patch("apps.galleries.tasks.build_gallery_download.delay"):
        response = client.post(gallery_url)
    assert response.status_code == 302
    assert GalleryDownload.objects.count() == 1


def test_gallery_download_defaults_and_session_authorization_are_not_plaintext():
    user = make_user()
    gallery = downloadable_gallery(user)
    ready_photo(gallery, user)
    request = session_request()
    download = pending_download(gallery, request)
    assert download.uuid
    assert download.status == GalleryDownload.Status.PENDING
    assert download.processed_photos == 0
    assert download.file_size == 0
    assert download.expires_at is None
    assert download.authorization_hash != request.session.session_key
    assert len(download.authorization_hash) == 64


def test_active_download_constraint_prevents_duplicate_generation():
    user = make_user()
    gallery = downloadable_gallery(user)
    ready_photo(gallery, user)
    request = session_request()
    first = pending_download(gallery, request)
    with pytest.raises(IntegrityError), transaction.atomic():
        GalleryDownload.objects.create(
            gallery=gallery,
            photo_count=1,
            content_fingerprint=first.content_fingerprint,
            authorization_hash=first.authorization_hash,
        )


def test_zip_generation_is_ordered_stored_sanitized_and_uses_zip64(no_path_storage):
    user = make_user()
    gallery = downloadable_gallery(user)
    second = ready_photo(gallery, user, "duplicate.jpg", b"second", order=20)
    first = ready_photo(gallery, user, "first.jpg", b"first", order=10)
    second.original_filename = "../../duplicate.jpg"
    first.original_filename = "../duplicate.jpg"
    second.save(update_fields=["original_filename", "updated_at"])
    first.save(update_fields=["original_filename", "updated_at"])
    request = session_request()
    download = pending_download(gallery, request)

    real_zip_file = zipfile.ZipFile
    constructor_options = {}

    def capture_zip_options(*args, **kwargs):
        constructor_options.update(kwargs)
        return real_zip_file(*args, **kwargs)

    with patch("apps.galleries.downloads.zipfile.ZipFile", side_effect=capture_zip_options):
        built = prepare_gallery_download(download.pk)
    built.refresh_from_db()
    assert built.status == GalleryDownload.Status.READY
    assert built.processed_photos == 2
    assert built.file.name == f"downloads/galleries/{gallery.uuid}/{built.uuid}.zip"
    assert no_path_storage.exists(built.file.name)
    assert constructor_options["allowZip64"] is True
    assert constructor_options["compression"] == zipfile.ZIP_STORED

    with zipfile.ZipFile(BytesIO(archive_bytes(built))) as archive:
        assert archive.namelist() == ["001_duplicate.jpg", "002_duplicate.jpg"]
        assert all(".." not in name and not name.startswith("/") for name in archive.namelist())
        assert archive.read("001_duplicate.jpg") == b"first"
        assert archive.read("002_duplicate.jpg") == b"second"
        assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())


def test_zip_member_name_never_contains_path_components():
    user = make_user()
    gallery = make_gallery(user)
    photo = ready_photo(gallery, user)
    photo.original_filename = "..\\..\\evil\x00.jpg"
    name = zip_member_name(photo, 1, 1)
    assert name == "001_evil.jpg"
    assert "/" not in name and "\\" not in name and ".." not in name


def test_missing_original_marks_download_error_without_partial_zip(no_path_storage):
    user = make_user()
    gallery = downloadable_gallery(user)
    photo = ready_photo(gallery, user)
    request = session_request()
    download = pending_download(gallery, request)
    no_path_storage.delete(photo.original_file.name)

    result = build_gallery_download.run(download.pk)
    download.refresh_from_db()
    assert result["status"] == "error"
    assert download.status == GalleryDownload.Status.ERROR
    assert not download.file
    assert not any(name.endswith(".zip") for name in no_path_storage.files)


def test_fingerprint_changes_for_add_delete_and_reorder():
    user = make_user()
    gallery = downloadable_gallery(user)
    first = ready_photo(gallery, user, "first.jpg", order=0)
    initial = gallery_content_fingerprint(gallery)
    second = ready_photo(gallery, user, "second.jpg", order=1)
    after_add = gallery_content_fingerprint(gallery)
    assert after_add != initial
    reorder_photos(
        gallery=gallery,
        photo_uuids=[second.uuid, first.uuid],
        reordered_by=user,
    )
    after_reorder = gallery_content_fingerprint(gallery)
    assert after_reorder != after_add
    second.delete()
    assert gallery_content_fingerprint(gallery) != after_reorder


def test_request_reuses_same_active_or_ready_download(
    no_path_storage, django_capture_on_commit_callbacks
):
    user = make_user()
    gallery = downloadable_gallery(user)
    ready_photo(gallery, user)
    request = session_request()
    with patch("apps.galleries.tasks.build_gallery_download.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            first, created = request_gallery_download(gallery=gallery, request=request)
            second, created_again = request_gallery_download(gallery=gallery, request=request)
    assert created is True and created_again is False
    assert first.pk == second.pk
    delay.assert_called_once_with(first.pk)

    built = prepare_gallery_download(first.pk)
    reused, reused_created = request_gallery_download(gallery=gallery, request=request)
    assert reused.pk == built.pk
    assert reused_created is False
    assert no_path_storage.exists(reused.file.name)


def test_status_and_file_are_session_bound_and_cross_gallery_safe(client):
    user = make_user()
    gallery = downloadable_gallery(user)
    other = downloadable_gallery(user, title="Other")
    ready_photo(gallery, user)
    session = client.session
    session["seed"] = True
    session.save()
    request = RequestFactory().get("/")
    request.session = client.session
    download = pending_download(gallery, request)
    prepare_gallery_download(download.pk)
    download.refresh_from_db()
    status_url = reverse("galleries_public:download_status", args=[gallery.slug, download.uuid])
    file_url = reverse("galleries_public:download_file", args=[gallery.slug, download.uuid])
    assert client.get(status_url).json() == {
        "status": "READY",
        "processed": 1,
        "total": 1,
        "ready": True,
        "file_size": download.file_size,
        "expires_at": download.expires_at.isoformat(),
    }
    assert client.get(file_url).status_code == 200
    cross_url = reverse("galleries_public:download_file", args=[other.slug, download.uuid])
    assert client.get(cross_url).status_code == 404

    other_client_response = client.__class__().get(status_url)
    assert other_client_response.status_code == 404


def test_gallery_download_endpoint_rejects_disabled_draft_and_archived(client):
    user = make_user()
    gallery = make_gallery(user, allow_gallery_download=False)
    ready_photo(gallery, user)
    url = reverse("galleries_public:download_request", args=[gallery.slug])
    assert client.post(url).status_code == 404
    gallery.allow_gallery_download = True
    for status in (Gallery.Status.DRAFT, Gallery.Status.ARCHIVED):
        gallery.status = status
        gallery.save(update_fields=["allow_gallery_download", "status", "updated_at"])
        assert client.post(url).status_code == 404


def test_public_request_persists_session_for_preparation_and_status(client):
    user = make_user()
    gallery = downloadable_gallery(user)
    ready_photo(gallery, user)
    with patch("apps.galleries.tasks.build_gallery_download.delay"):
        response = client.post(reverse("galleries_public:download_request", args=[gallery.slug]))
    assert response.status_code == 302
    assert client.get(response.url).status_code == 200
    download = GalleryDownload.objects.get()
    status_url = reverse("galleries_public:download_status", args=[gallery.slug, download.uuid])
    assert client.get(status_url).status_code == 200
    assert download.authorization_hash != client.session.session_key


def test_expiration_cleanup_deletes_zip_and_is_idempotent(no_path_storage, capsys):
    user = make_user()
    gallery = downloadable_gallery(user)
    ready_photo(gallery, user)
    download = pending_download(gallery, session_request())
    prepare_gallery_download(download.pk)
    download.refresh_from_db()
    stored_name = download.file.name
    download.expires_at = timezone.now() - timedelta(seconds=1)
    download.save(update_fields=["expires_at", "updated_at"])

    call_command("cleanup_gallery_downloads")
    assert "Expired downloads cleaned: 1" in capsys.readouterr().out
    download.refresh_from_db()
    assert download.status == GalleryDownload.Status.EXPIRED
    assert not download.file
    assert not no_path_storage.exists(stored_name)
    call_command("cleanup_gallery_downloads")
    assert "Expired downloads cleaned: 0" in capsys.readouterr().out


def test_obsolete_fingerprint_blocks_ready_download(client):
    user = make_user()
    gallery = downloadable_gallery(user)
    ready_photo(gallery, user)
    session = client.session
    session["seed"] = True
    session.save()
    request = RequestFactory().get("/")
    request.session = client.session
    download = pending_download(gallery, request)
    prepare_gallery_download(download.pk)
    ready_photo(gallery, user, "new.jpg")
    url = reverse("galleries_public:download_file", args=[gallery.slug, download.uuid])
    assert client.get(url).status_code == 404


def test_r2_individual_and_zip_urls_use_attachment_and_ttl(client, settings, no_path_storage):
    user = make_user()
    gallery = downloadable_gallery(user)
    photo = ready_photo(gallery, user)
    settings.STORAGE_BACKEND = "r2"
    settings.MAPACHE_DOWNLOAD_URL_TTL = 777
    individual = client.get(
        reverse("galleries_public:photo_download", args=[gallery.slug, photo.uuid])
    )
    assert individual.status_code == 302
    name, expiry, parameters = no_path_storage.url_calls[-1]
    assert name == photo.original_file.name
    assert expiry == 777
    assert parameters["ResponseContentDisposition"].startswith("attachment;")

    session = client.session
    session["seed"] = True
    session.save()
    request = RequestFactory().get("/")
    request.session = client.session
    download = pending_download(gallery, request)
    settings.STORAGE_BACKEND = "local"
    prepare_gallery_download(download.pk)
    settings.STORAGE_BACKEND = "r2"
    response = client.get(
        reverse("galleries_public:download_file", args=[gallery.slug, download.uuid])
    )
    assert response.status_code == 302
    assert no_path_storage.url_calls[-1][1] == 777
    assert no_path_storage.url_calls[-1][2]["ResponseContentType"] == "application/zip"


def test_invalidation_expires_download_deletes_file_and_audits(no_path_storage):
    user = make_user()
    gallery = downloadable_gallery(user)
    ready_photo(gallery, user)
    download = pending_download(gallery, session_request())
    prepare_gallery_download(download.pk)
    download.refresh_from_db()
    stored_name = download.file.name
    assert invalidate_gallery_downloads(gallery=gallery, invalidated_by=user) == 1
    download.refresh_from_db()
    assert download.status == GalleryDownload.Status.EXPIRED
    assert not no_path_storage.exists(stored_name)
    assert AuditLog.objects.filter(
        action="GALLERY_DOWNLOADS_INVALIDATED", object_id=str(gallery.uuid)
    ).exists()


def test_download_ctas_appear_only_when_flags_are_enabled(client):
    user = make_user()
    gallery = downloadable_gallery(user)
    photo = ready_photo(gallery, user)
    photo.optimized_file.save(f"{photo.uuid}.webp", ContentFile(b"webp"))
    page = client.get(reverse("galleries_public:detail", args=[gallery.slug])).content.decode()
    assert reverse("galleries_public:photo_download", args=[gallery.slug, photo.uuid]) in page
    assert reverse("galleries_public:download_request", args=[gallery.slug]) in page
    gallery.allow_photo_download = False
    gallery.allow_gallery_download = False
    gallery.save(update_fields=["allow_photo_download", "allow_gallery_download", "updated_at"])
    page = client.get(reverse("galleries_public:detail", args=[gallery.slug])).content.decode()
    assert "Descargar galería" not in page
    assert "Descargar ↓" not in page


def test_download_tasks_are_routed_to_downloads_queue(settings):
    assert settings.CELERY_TASK_ROUTES["apps.galleries.tasks.*"] == {"queue": "downloads"}
    assert build_gallery_download.name == "apps.galleries.tasks.build_gallery_download"
