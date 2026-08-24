from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.urls import reverse
from django.utils import timezone

from apps.galleries.direct_uploads.services import (
    abort_upload,
    cleanup_expired_uploads,
    confirm_upload,
    generate_part_urls,
    initialize_uploads,
)
from apps.galleries.models import GalleryUploadBatch, GalleryUploadItem, Photo
from apps.mapache_ai.models import GalleryAISettings
from apps.media_processing.services import process_photo_image
from tests.factories import make_gallery, make_user
from tests.image_helpers import make_test_image

pytestmark = pytest.mark.django_db


class FakeR2Client:
    def __init__(self):
        self.objects = {}
        self.multipart = {}
        self.aborted = []
        self.deleted = []
        self.presigned = []

    def presign_put(self, *, key, content_type, expires):
        self.presigned.append(("PUT", key, content_type, expires))
        return f"https://r2.test/put/{key}?signed=yes"

    def create_multipart(self, *, key, content_type):
        upload_id = f"upload-{len(self.multipart) + 1}"
        self.multipart[upload_id] = {"key": key, "content_type": content_type, "parts": {}}
        return upload_id

    def presign_part(self, *, key, upload_id, part_number, expires):
        self.presigned.append(("PART", key, upload_id, part_number, expires))
        return f"https://r2.test/part/{upload_id}/{part_number}?signed=yes"

    def list_parts(self, *, key, upload_id):
        upload = self.multipart[upload_id]
        assert upload["key"] == key
        return [
            {"part_number": number, "etag": etag}
            for number, etag in sorted(upload["parts"].items())
        ]

    def complete_multipart(self, *, key, upload_id, parts):
        upload = self.multipart[upload_id]
        assert upload["key"] == key
        assert parts == self.list_parts(key=key, upload_id=upload_id)
        self.objects[key] = sum(upload.get("sizes", {}).values())

    def abort_multipart(self, *, key, upload_id):
        self.aborted.append((key, upload_id))

    def head(self, *, key):
        if key not in self.objects:
            raise FileNotFoundError(key)
        return {"ContentLength": self.objects[key]}

    def delete(self, *, key):
        self.deleted.append(key)
        self.objects.pop(key, None)


@pytest.fixture
def direct_settings(settings):
    settings.MAPACHE_DIRECT_UPLOAD_ENABLED = True
    settings.STORAGE_BACKEND = "r2"
    settings.MAPACHE_DIRECT_UPLOAD_MAX_FILES = 5000
    settings.MAPACHE_DIRECT_UPLOAD_MAX_TOTAL_BYTES = 1024 * 1024 * 1024
    settings.MAPACHE_MAX_PHOTO_SIZE_MB = 100
    settings.MAPACHE_MULTIPART_UPLOAD_THRESHOLD_MB = 20
    settings.MAPACHE_MULTIPART_PART_SIZE_MB = 10
    settings.MAPACHE_UPLOAD_URL_TTL = 900
    settings.MAPACHE_UPLOAD_SESSION_TTL = 86400


def metadata(name="DSC_001.JPG", size=1024, content_type="image/jpeg", modified=123):
    return [{"name": name, "size": size, "type": content_type, "last_modified": modified}]


def init_single(user, gallery, fake):
    return initialize_uploads(
        gallery=gallery,
        user=user,
        metadata=metadata(),
        client=fake,
    )


def test_direct_init_requires_authentication(client, direct_settings):
    user = make_user()
    gallery = make_gallery(user)
    response = client.post(
        reverse("galleries_dashboard:upload_init", args=[gallery.uuid]),
        data={"files": metadata()},
        content_type="application/json",
    )
    assert response.status_code == 302


def test_init_generates_server_key_and_safe_presigned_response(client, direct_settings):
    user = make_user()
    gallery = make_gallery(user)
    fake = FakeR2Client()
    client.force_login(user)
    with patch(
        "apps.galleries.direct_uploads.services.get_direct_upload_client",
        return_value=fake,
    ):
        response = client.post(
            reverse("galleries_dashboard:upload_init", args=[gallery.uuid]),
            data={"files": metadata("../../private.JPG")},
            content_type="application/json",
        )
    assert response.status_code == 201
    payload = response.json()
    item = GalleryUploadItem.objects.get(uuid=payload["items"][0]["upload_item_uuid"])
    assert item.object_key.startswith(f"galleries/{gallery.uuid}/originals/")
    assert item.object_key.endswith(".jpg")
    assert "private" not in item.object_key
    assert payload["items"][0]["upload_url"].startswith("https://r2.test/")
    serialized = response.content.decode()
    assert "ACCESS_KEY" not in serialized
    assert "SECRET" not in serialized
    assert item.original_filename == "private.JPG"
    assert fake.presigned[0][-1] == 900


@pytest.mark.parametrize(
    "bad_metadata",
    [
        metadata("virus.exe"),
        metadata("photo.jpg", content_type="application/octet-stream"),
        metadata("photo.jpg", size=101 * 1024 * 1024),
    ],
)
def test_init_rejects_invalid_metadata(direct_settings, bad_metadata):
    user = make_user()
    gallery = make_gallery(user)
    with pytest.raises(ValidationError):
        initialize_uploads(
            gallery=gallery,
            user=user,
            metadata=bad_metadata,
            client=FakeR2Client(),
        )


def test_init_enforces_file_count_and_total_size(settings, direct_settings):
    user = make_user()
    gallery = make_gallery(user)
    settings.MAPACHE_DIRECT_UPLOAD_MAX_FILES = 1
    with pytest.raises(ValidationError):
        initialize_uploads(
            gallery=gallery,
            user=user,
            metadata=metadata("one.jpg") + metadata("two.jpg"),
            client=FakeR2Client(),
        )
    settings.MAPACHE_DIRECT_UPLOAD_MAX_FILES = 10
    settings.MAPACHE_DIRECT_UPLOAD_MAX_TOTAL_BYTES = 1500
    with pytest.raises(ValidationError):
        initialize_uploads(
            gallery=gallery,
            user=user,
            metadata=metadata(size=1600),
            client=FakeR2Client(),
        )


def test_single_complete_verifies_head_and_is_idempotent(
    direct_settings, django_capture_on_commit_callbacks
):
    user = make_user()
    gallery = make_gallery(user)
    fake = FakeR2Client()
    batch, _payload = init_single(user, gallery, fake)
    item = batch.items.get()
    fake.objects[item.object_key] = item.expected_size
    with (
        patch("apps.media_processing.tasks.process_photo.delay") as queued,
        django_capture_on_commit_callbacks(execute=True),
    ):
        first = confirm_upload(item=item, user=user, client=fake)
        second = confirm_upload(item=item, user=user, client=fake)
    assert first.photo_id == second.photo_id
    assert Photo.objects.count() == 1
    assert first.photo.original_file.name == item.object_key
    assert first.photo.uuid == item.reserved_photo_uuid
    assert first.photo.processing_status == Photo.ProcessingStatus.PENDING
    queued.assert_called_once_with(first.photo_id)


@pytest.mark.parametrize("object_size", [None, 100])
def test_single_complete_rejects_missing_or_wrong_size(direct_settings, object_size):
    user = make_user()
    gallery = make_gallery(user)
    fake = FakeR2Client()
    batch, _payload = init_single(user, gallery, fake)
    item = batch.items.get()
    if object_size is not None:
        fake.objects[item.object_key] = object_size
    with pytest.raises(ValidationError):
        confirm_upload(item=item, user=user, client=fake)
    assert not Photo.objects.exists()


def test_multipart_parts_resume_complete_and_abort(direct_settings):
    user = make_user()
    gallery = make_gallery(user)
    fake = FakeR2Client()
    size = 25 * 1024 * 1024
    batch, payload = initialize_uploads(
        gallery=gallery,
        user=user,
        metadata=metadata("large.jpg", size=size),
        client=fake,
    )
    item = batch.items.get()
    assert payload[0]["mode"] == GalleryUploadItem.UploadMode.MULTIPART
    fake.multipart[item.multipart_upload_id]["parts"] = {1: '"etag-1"', 2: '"etag-2"'}
    fake.multipart[item.multipart_upload_id]["sizes"] = {
        1: 10 * 1024 * 1024,
        2: 10 * 1024 * 1024,
        3: 5 * 1024 * 1024,
    }
    urls, existing = generate_part_urls(
        item=item,
        part_numbers=[1, 2, 3],
        user=user,
        client=fake,
    )
    assert [part["part_number"] for part in existing] == [1, 2]
    assert [part["part_number"] for part in urls] == [3]
    fake.multipart[item.multipart_upload_id]["parts"][3] = '"etag-3"'
    with patch("apps.media_processing.tasks.process_photo.delay"):
        confirmed = confirm_upload(
            item=item,
            user=user,
            parts=fake.list_parts(key=item.object_key, upload_id=item.multipart_upload_id),
            client=fake,
        )
    assert confirmed.photo_id

    other_batch, _ = initialize_uploads(
        gallery=gallery,
        user=user,
        metadata=metadata("cancel.jpg", size=size, modified=999),
        client=fake,
    )
    cancelled = other_batch.items.get()
    abort_upload(item=cancelled, user=user, client=fake)
    assert (cancelled.object_key, cancelled.multipart_upload_id) in fake.aborted


def test_expiration_and_orphan_cleanup_preserve_confirmed_photo(direct_settings):
    user = make_user()
    gallery = make_gallery(user)
    fake = FakeR2Client()
    batch, _ = init_single(user, gallery, fake)
    orphan = batch.items.get()
    fake.objects[orphan.object_key] = orphan.expected_size
    orphan.expires_at = timezone.now() - timedelta(seconds=1)
    orphan.save(update_fields=["expires_at"])
    assert cleanup_expired_uploads(client=fake) == 1
    orphan.refresh_from_db()
    assert orphan.status == GalleryUploadItem.Status.EXPIRED
    assert orphan.object_key in fake.deleted

    fresh_batch, _ = initialize_uploads(
        gallery=gallery,
        user=user,
        metadata=metadata("valid.jpg", modified=456),
        client=fake,
    )
    valid = fresh_batch.items.get()
    fake.objects[valid.object_key] = valid.expected_size
    with patch("apps.media_processing.tasks.process_photo.delay"):
        valid = confirm_upload(item=valid, user=user, client=fake)
    valid.expires_at = timezone.now() - timedelta(seconds=1)
    valid.save(update_fields=["expires_at"])
    assert cleanup_expired_uploads(client=fake) == 0
    assert Photo.objects.filter(pk=valid.photo_id).exists()
    assert valid.object_key not in fake.deleted


def test_security_blocks_foreign_complete_abort_and_resume(client, direct_settings):
    owner = make_user("owner")
    attacker = make_user("attacker")
    gallery = make_gallery(owner)
    fake = FakeR2Client()
    batch, _ = init_single(owner, gallery, fake)
    item = batch.items.get()
    client.force_login(attacker)
    for url in [
        reverse("direct_uploads:complete", args=[item.uuid]),
        reverse("direct_uploads:abort", args=[item.uuid]),
        reverse("direct_uploads:resume", args=[batch.uuid]),
    ]:
        method = client.get if url.endswith(f"{batch.uuid}/") else client.post
        assert method(url, data={}, content_type="application/json").status_code == 404
    assert not Photo.objects.exists()


def test_batch_cannot_be_reused_in_another_gallery(direct_settings):
    user = make_user()
    first_gallery = make_gallery(user, title="First")
    second_gallery = make_gallery(user, title="Second")
    fake = FakeR2Client()
    batch, _ = init_single(user, first_gallery, fake)
    with pytest.raises(ValidationError):
        initialize_uploads(
            gallery=second_gallery,
            user=user,
            metadata=metadata("other.jpg"),
            batch_uuid=batch.uuid,
            client=fake,
        )
    assert batch.items.filter(gallery=first_gallery).count() == 1


def test_feature_flag_keeps_direct_endpoints_disabled(client, settings):
    settings.MAPACHE_DIRECT_UPLOAD_ENABLED = False
    settings.STORAGE_BACKEND = "local"
    user = make_user()
    gallery = make_gallery(user)
    client.force_login(user)
    response = client.post(
        reverse("galleries_dashboard:upload_init", args=[gallery.uuid]),
        data={"files": metadata()},
        content_type="application/json",
    )
    assert response.status_code == 404


def test_dashboard_switches_between_traditional_and_direct_ui(client, settings):
    user = make_user()
    gallery = make_gallery(user)
    client.force_login(user)
    url = reverse("galleries_dashboard:photos", args=[gallery.uuid])
    settings.MAPACHE_DIRECT_UPLOAD_ENABLED = False
    settings.STORAGE_BACKEND = "local"
    traditional = client.get(url)
    assert traditional.status_code == 200
    assert b'data-direct-upload="false"' in traditional.content
    assert reverse("galleries_dashboard:photos_upload", args=[gallery.uuid]).encode() in (
        traditional.content
    )
    settings.MAPACHE_DIRECT_UPLOAD_ENABLED = True
    settings.STORAGE_BACKEND = "r2"
    direct = client.get(url)
    assert direct.status_code == 200
    assert b'data-direct-upload="true"' in direct.content
    assert reverse("galleries_dashboard:upload_init", args=[gallery.uuid]).encode() in (
        direct.content
    )


def test_batch_audit_is_not_created_per_part(direct_settings):
    user = make_user()
    gallery = make_gallery(user)
    fake = FakeR2Client()
    initialize_uploads(
        gallery=gallery,
        user=user,
        metadata=metadata("large.jpg", size=25 * 1024 * 1024),
        client=fake,
    )
    assert GalleryUploadBatch.objects.count() == 1
    assert user.audit_logs.filter(action="UPLOAD_BATCH_CREATED").count() == 1


def test_resume_endpoint_returns_existing_parts_without_secrets(client, direct_settings):
    user = make_user()
    gallery = make_gallery(user)
    fake = FakeR2Client()
    batch, _ = initialize_uploads(
        gallery=gallery,
        user=user,
        metadata=metadata("large.jpg", size=25 * 1024 * 1024),
        client=fake,
    )
    item = batch.items.get()
    fake.multipart[item.multipart_upload_id]["parts"] = {1: '"done"'}
    client.force_login(user)
    with patch(
        "apps.galleries.direct_uploads.views.get_direct_upload_client",
        return_value=fake,
    ):
        response = client.get(reverse("direct_uploads:resume", args=[batch.uuid]))
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["uploaded_parts"] == [{"part_number": 1, "etag": '"done"'}]
    serialized = response.content.decode()
    assert item.object_key not in serialized
    assert item.multipart_upload_id not in serialized
    assert "upload_url" not in serialized


def test_direct_complete_uses_no_path_pipeline_and_keeps_ai_hooks(
    settings, direct_settings, django_capture_on_commit_callbacks
):
    settings.STORAGES = {
        "default": {"BACKEND": "tests.storage_backends.NoPathMemoryStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    default_storage.files.clear()
    user = make_user()
    gallery = make_gallery(user)
    GalleryAISettings.objects.create(
        gallery=gallery,
        enabled=True,
        face_search_enabled=True,
        bib_search_enabled=True,
    )
    image = make_test_image("direct.jpg", size=(90, 60))
    size = image.size
    fake = FakeR2Client()
    batch, _ = initialize_uploads(
        gallery=gallery,
        user=user,
        metadata=metadata("direct.jpg", size=size),
        client=fake,
    )
    item = batch.items.get()
    default_storage.save(item.object_key, image)
    fake.objects[item.object_key] = size
    with patch("apps.media_processing.tasks.process_photo.delay"):
        confirmed = confirm_upload(item=item, user=user, client=fake)
    with (
        patch("apps.mapache_ai.tasks.index_photo_faces.delay") as face_task,
        patch("apps.mapache_ai.bib.tasks.index_photo_bibs.delay") as bib_task,
        django_capture_on_commit_callbacks(execute=True),
    ):
        processed = process_photo_image(confirmed.photo_id)
    assert processed.processing_status == Photo.ProcessingStatus.READY
    assert default_storage.exists(processed.optimized_file.name)
    assert default_storage.exists(processed.thumbnail_file.name)
    with pytest.raises(NotImplementedError):
        _path = processed.original_file.path
    face_task.assert_called_once_with(processed.pk)
    bib_task.assert_called_once_with(processed.pk)
