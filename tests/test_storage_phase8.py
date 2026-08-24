from unittest.mock import patch

import pytest
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, default_storage
from django.core.management import call_command
from django.test import RequestFactory
from django.urls import reverse

from apps.core.management.commands.migrate_media_to_storage import copy_media_objects
from apps.core.media_delivery import get_original_download_url, get_photo_delivery_url
from apps.core.storage_backends import R2MediaStorage
from apps.galleries.models import Gallery, Photo
from apps.galleries.services import add_photo, delete_photo, delete_photos, publish_gallery
from apps.mapache_ai.bib.fake import FakeBibRecognitionEngine
from apps.mapache_ai.bib.services import index_photo_bibs_now
from apps.mapache_ai.engines.fake import FakeFaceEngine
from apps.mapache_ai.models import BibPhotoAnalysis, GalleryAISettings, PhotoFaceIndex
from apps.mapache_ai.services import index_photo_faces_now
from apps.media_processing.services import process_photo_image, reprocess_photo
from tests.factories import make_gallery, make_user
from tests.image_helpers import make_test_image
from tests.storage_backends import NoPathMemoryStorage

pytestmark = pytest.mark.django_db


@pytest.fixture
def no_path_storage(settings):
    settings.STORAGE_BACKEND = "local"
    settings.STORAGES = {
        "default": {"BACKEND": "tests.storage_backends.NoPathMemoryStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    storage = default_storage
    storage.files.clear()
    return storage


def no_path_photo(gallery, user, name="runner.jpg"):
    return add_photo(
        gallery=gallery,
        original_file=make_test_image(name),
        uploaded_by=user,
        mime_type="image/jpeg",
    )


def enable_ai(gallery):
    return GalleryAISettings.objects.create(
        gallery=gallery,
        enabled=True,
        face_search_enabled=True,
        bib_search_enabled=True,
    )


def test_processing_face_bib_reprocess_and_delete_work_without_path(no_path_storage):
    user = make_user()
    gallery = make_gallery(user)
    enable_ai(gallery)
    photo = no_path_photo(gallery, user)
    with pytest.raises(NotImplementedError):
        _path = photo.original_file.path

    processed = process_photo_image(photo.pk)
    assert processed.processing_status == Photo.ProcessingStatus.READY
    assert no_path_storage.exists(processed.original_file.name)
    assert no_path_storage.exists(processed.optimized_file.name)
    assert no_path_storage.exists(processed.thumbnail_file.name)
    assert index_photo_faces_now(processed.pk, engine=FakeFaceEngine()) == 1
    assert index_photo_bibs_now(processed.pk, engine=FakeBibRecognitionEngine()) == 1
    assert PhotoFaceIndex.objects.get(photo=processed).face_count == 1
    assert BibPhotoAnalysis.objects.get(photo=processed).detected_count == 1

    keys_before = set(no_path_storage.files)
    with patch("apps.media_processing.tasks.process_photo.delay"):
        reprocess_photo(photo=processed, requested_by=user)
    reprocessed = process_photo_image(processed.pk)
    assert reprocessed.processing_status == Photo.ProcessingStatus.READY
    assert set(no_path_storage.files) == keys_before

    names = [
        processed.original_file.name,
        processed.optimized_file.name,
        processed.thumbnail_file.name,
    ]
    delete_photo(photo=processed, deleted_by=user)
    assert not Photo.objects.filter(pk=processed.pk).exists()
    assert all(not no_path_storage.exists(name) for name in names)


def test_bulk_delete_removes_every_object_without_path(no_path_storage):
    user = make_user()
    gallery = make_gallery(user)
    first = process_photo_image(no_path_photo(gallery, user, "first.jpg").pk)
    second = process_photo_image(no_path_photo(gallery, user, "second.jpg").pk)
    names = [
        field.name
        for photo in (first, second)
        for field in (photo.original_file, photo.optimized_file, photo.thumbnail_file)
    ]
    assert (
        delete_photos(
            gallery=gallery,
            photo_uuids=[first.uuid, second.uuid],
            deleted_by=user,
        )
        == 2
    )
    assert all(not no_path_storage.exists(name) for name in names)


def test_public_delivery_original_policy_and_nonpublished_denial(no_path_storage):
    user = make_user()
    gallery = make_gallery(user)
    photo = process_photo_image(no_path_photo(gallery, user).pk)
    request = RequestFactory().get("/")
    request.session = {}

    with pytest.raises(PermissionDenied):
        get_photo_delivery_url(photo=photo, variant="optimized", request=request, audience="public")
    publish_gallery(gallery=gallery, published_by=user)
    gallery.refresh_from_db()
    photo.gallery = gallery
    optimized = get_photo_delivery_url(
        photo=photo, variant="optimized", request=request, audience="public"
    )
    assert optimized == reverse("core_media:local_photo", args=[photo.uuid, "optimized"])
    gallery.visibility = Gallery.Visibility.UNLISTED
    gallery.save(update_fields=["visibility"])
    assert get_photo_delivery_url(
        photo=photo, variant="thumbnail", request=request, audience="public"
    ) == reverse("core_media:local_photo", args=[photo.uuid, "thumbnail"])
    with pytest.raises(PermissionDenied):
        get_photo_delivery_url(
            photo=photo,
            variant="original",
            request=request,
            audience="public",
            allow_original=True,
        )

    gallery.status = Gallery.Status.ARCHIVED
    gallery.save(update_fields=["status"])
    with pytest.raises(PermissionDenied):
        get_photo_delivery_url(photo=photo, variant="thumbnail", request=request, audience="public")


def test_private_pin_local_media_requires_session_and_signed_ttl(client, no_path_storage, settings):
    user = make_user()
    gallery = make_gallery(user, visibility=Gallery.Visibility.PRIVATE_PIN)
    gallery.set_pin("4821")
    gallery.save(update_fields=["pin_hash"])
    publish_gallery(gallery=gallery, published_by=user)
    photo = process_photo_image(no_path_photo(gallery, user).pk)
    request = RequestFactory().get("/")
    request.session = {}
    with pytest.raises(PermissionDenied):
        get_photo_delivery_url(photo=photo, variant="thumbnail", request=request, audience="public")
    request.session[f"gallery_access_{gallery.uuid}"] = True
    private_url = get_photo_delivery_url(
        photo=photo, variant="thumbnail", request=request, audience="public"
    )
    assert "storage.test" not in private_url

    blocked = client.get(private_url)
    assert blocked.status_code == 404
    client.post(reverse("galleries_public:access", args=[gallery.slug]), {"pin": "4821"})
    with patch("apps.core.media_views.signing.loads", wraps=signing.loads) as loads:
        delivered = client.get(private_url)
    assert delivered.status_code == 200
    assert delivered["Content-Type"] == "image/webp"
    assert loads.call_args.kwargs["max_age"] == settings.MAPACHE_PRIVATE_MEDIA_URL_TTL


def test_r2_delivery_uses_configured_public_and_private_expiry(no_path_storage, settings):
    user = make_user()
    public = make_gallery(user, title="Public")
    publish_gallery(gallery=public, published_by=user)
    public_photo = process_photo_image(no_path_photo(public, user, "public.jpg").pk)
    private = make_gallery(user, title="Private", visibility=Gallery.Visibility.PRIVATE_PIN)
    private.set_pin("4821")
    private.save(update_fields=["pin_hash"])
    publish_gallery(gallery=private, published_by=user)
    private_photo = process_photo_image(no_path_photo(private, user, "private.jpg").pk)
    settings.STORAGE_BACKEND = "r2"
    settings.MAPACHE_PUBLIC_MEDIA_URL_TTL = 3600
    settings.MAPACHE_PRIVATE_MEDIA_URL_TTL = 900
    request = RequestFactory().get("/")
    request.session = {f"gallery_access_{private.uuid}": True}

    public_url = get_photo_delivery_url(photo=public_photo, variant="optimized", request=request)
    private_url = get_photo_delivery_url(photo=private_photo, variant="optimized", request=request)
    assert public_url.endswith("expires=3600")
    assert private_url.endswith("expires=900")


def test_dashboard_can_request_original_but_public_html_never_exposes_it(client, no_path_storage):
    user = make_user()
    gallery = publish_gallery(gallery=make_gallery(user), published_by=user)
    photo = process_photo_image(no_path_photo(gallery, user).pk)
    request = RequestFactory().get("/")
    request.user = user
    original_url = get_original_download_url(photo=photo, request=request)
    assert original_url == reverse("core_media:local_photo", args=[photo.uuid, "original"])
    public_html = client.get(
        reverse("galleries_public:detail", args=[gallery.slug])
    ).content.decode()
    assert photo.original_file.name not in public_html
    assert "originals" not in public_html


def test_local_media_endpoint_enforces_public_and_admin_policy(client, no_path_storage):
    user = make_user()
    gallery = make_gallery(user)
    photo = process_photo_image(no_path_photo(gallery, user).pk)
    optimized_url = reverse("core_media:local_photo", args=[photo.uuid, "optimized"])
    original_url = reverse("core_media:local_photo", args=[photo.uuid, "original"])
    assert client.get(optimized_url).status_code == 404
    publish_gallery(gallery=gallery, published_by=user)
    assert client.get(optimized_url).status_code == 200
    assert client.get(photo.optimized_file.url).status_code == 404
    assert client.get(original_url).status_code == 404
    client.force_login(user)
    assert client.get(original_url).status_code == 200


def test_check_storage_works_with_backend_without_path(no_path_storage, capsys):
    call_command("check_storage")
    output = capsys.readouterr().out
    assert "Almacenamiento: LOCAL" in output
    assert "Escritura: CORRECTA" in output
    assert "Lectura: CORRECTA" in output
    assert "Eliminación: CORRECTA" in output
    assert no_path_storage.files == {}


def test_copy_media_dry_run_copy_skip_and_failure_are_isolated(tmp_path):
    source = FileSystemStorage(location=tmp_path / "source")
    destination = NoPathMemoryStorage()
    source.save("galleries/a/originals/one.jpg", ContentFile(b"one"))
    source.save("galleries/a/originals/two.jpg", ContentFile(b"two"))
    names = [
        "galleries/a/originals/one.jpg",
        "galleries/a/originals/missing.jpg",
        "galleries/a/originals/two.jpg",
    ]
    dry = copy_media_objects(source=source, destination=destination, names=names, dry_run=True)
    assert dry == {"copied": 0, "skipped": 0, "failed": 1, "bytes": 0}
    assert destination.files == {}

    copied = copy_media_objects(source=source, destination=destination, names=names)
    assert copied == {"copied": 2, "skipped": 0, "failed": 1, "bytes": 6}
    skipped = copy_media_objects(source=source, destination=destination, names=names[:1])
    assert skipped == {"copied": 0, "skipped": 1, "failed": 0, "bytes": 0}

    source.save("galleries/a/originals/conflict.jpg", ContentFile(b"source"))
    destination.save("galleries/a/originals/conflict.jpg", ContentFile(b"destination"))
    conflict = copy_media_objects(
        source=source,
        destination=destination,
        names=["galleries/a/originals/conflict.jpg"],
    )
    assert conflict == {"copied": 0, "skipped": 0, "failed": 1, "bytes": 0}
    with destination.open("galleries/a/originals/conflict.jpg", "rb") as stored:
        assert stored.read() == b"destination"


def test_migrate_media_command_filters_gallery_and_reports_summary(
    tmp_path, no_path_storage, capsys
):
    user = make_user()
    selected = make_gallery(user, title="Selected")
    other = make_gallery(user, title="Other")
    selected_photo = no_path_photo(selected, user, "selected.jpg")
    other_photo = no_path_photo(other, user, "other.jpg")
    source = FileSystemStorage(location=tmp_path / "legacy")
    source.save(selected_photo.original_file.name, ContentFile(b"selected"))
    source.save(other_photo.original_file.name, ContentFile(b"other"))
    no_path_storage.files.clear()

    call_command(
        "migrate_media_to_storage",
        gallery_uuid=str(selected.uuid),
        source_root=str(tmp_path / "legacy"),
        dry_run=True,
    )
    dry_run_output = capsys.readouterr().out
    assert no_path_storage.files == {}
    assert "Modo: SIMULACIÓN" in dry_run_output

    call_command(
        "migrate_media_to_storage",
        gallery_uuid=str(selected.uuid),
        source_root=str(tmp_path / "legacy"),
    )
    output = capsys.readouterr().out
    assert no_path_storage.exists(selected_photo.original_file.name)
    assert not no_path_storage.exists(other_photo.original_file.name)
    assert "Copiados: 1" in output
    assert "Fallidos: 0" in output


def test_r2_backend_generates_v4_signed_urls_and_safe_object_headers(settings):
    settings.MAPACHE_PRIVATE_MEDIA_URL_TTL = 900
    storage = R2MediaStorage(
        bucket_name="mapache-test",
        access_key="test-access",
        secret_key="test-secret",
        endpoint_url="https://account.r2.cloudflarestorage.com",
        region_name="auto",
    )
    url = storage.url("galleries/abc/optimized/photo.webp", expire=321)
    assert url.startswith("https://account.r2.cloudflarestorage.com/")
    assert "X-Amz-Signature=" in url
    assert "X-Amz-Expires=321" in url
    derivative = storage._get_write_parameters(
        "galleries/abc/optimized/photo.webp", ContentFile(b"webp")
    )
    original = storage._get_write_parameters(
        "galleries/abc/originals/photo.jpg", ContentFile(b"jpeg")
    )
    assert derivative["ContentType"] == "image/webp"
    assert derivative["ContentDisposition"] == "inline"
    assert derivative["CacheControl"] == "private, max-age=900"
    assert original["ContentType"] == "image/jpeg"
    assert original["CacheControl"] == "private, no-store"
