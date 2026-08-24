from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.galleries.models import Photo
from tests.factories import make_gallery, make_user
from tests.image_helpers import make_test_image

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def temporary_media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def test_anonymous_upload_status_and_retry_are_protected(client):
    user = make_user()
    gallery = make_gallery(user)
    photo = Photo.objects.create(
        gallery=gallery,
        original_file="galleries/broken.jpg",
        filename="broken.jpg",
        original_filename="broken.jpg",
        processing_status=Photo.ProcessingStatus.ERROR,
        uploaded_by=user,
    )
    targets = [
        ("get", reverse("galleries_dashboard:photos", args=[gallery.uuid])),
        ("post", reverse("galleries_dashboard:photos_upload", args=[gallery.uuid])),
        ("get", reverse("galleries_dashboard:photos_status", args=[gallery.uuid])),
        (
            "post",
            reverse("galleries_dashboard:photo_retry", args=[gallery.uuid, photo.uuid]),
        ),
    ]

    for method, url in targets:
        response = getattr(client, method)(url)
        assert response.status_code == 302
        assert reverse("accounts:login") in response.url


def test_multiple_upload_accepts_valid_files_and_rejects_bad_one_independently(client):
    user = make_user()
    gallery = make_gallery(user)
    client.force_login(user)
    files = [
        make_test_image("one.jpg"),
        SimpleUploadedFile("bad.jpg", b"not-image", content_type="image/jpeg"),
        make_test_image("two.png", image_format="PNG"),
    ]

    with patch("apps.media_processing.tasks.process_photo.delay") as delay:
        response = client.post(
            reverse("galleries_dashboard:photos_upload", args=[gallery.uuid]),
            {"photos": files},
        )

    assert response.status_code == 302
    assert gallery.photos.count() == 2
    assert list(gallery.photos.values_list("sort_order", flat=True)) == [0, 1]
    assert delay.call_count == 2
    page = client.get(reverse("galleries_dashboard:photos", args=[gallery.uuid]))
    content = page.content.decode()
    assert "3 fotografías recibidas" in content
    assert "2 aceptadas" in content
    assert "1 rechazadas" in content
    assert "bad.jpg" in content


def test_authenticated_status_endpoint_returns_only_processing_data(client):
    user = make_user()
    gallery = make_gallery(user)
    Photo.objects.create(
        gallery=gallery,
        original_file="galleries/pending.jpg",
        filename="pending.jpg",
        original_filename="pending.jpg",
        uploaded_by=user,
    )
    client.force_login(user)

    response = client.get(reverse("galleries_dashboard:photos_status", args=[gallery.uuid]))
    data = response.json()

    assert response.status_code == 200
    assert data["total"] == 1
    assert data["pending"] == 1
    assert set(data) == {"total", "pending", "processing", "ready", "error", "photos"}
    assert "original_file" not in data["photos"][0]


def test_retry_error_photo_is_post_only_and_enqueues(client):
    user = make_user()
    gallery = make_gallery(user)
    photo = Photo.objects.create(
        gallery=gallery,
        original_file="galleries/error.jpg",
        filename="error.jpg",
        original_filename="error.jpg",
        processing_status=Photo.ProcessingStatus.ERROR,
        processing_error="Fallo",
        uploaded_by=user,
    )
    client.force_login(user)
    url = reverse("galleries_dashboard:photo_retry", args=[gallery.uuid, photo.uuid])

    assert client.get(url).status_code == 405
    with patch("apps.media_processing.tasks.process_photo.delay") as delay:
        response = client.post(url)
    photo.refresh_from_db()

    assert response.status_code == 302
    assert photo.processing_status == Photo.ProcessingStatus.PENDING
    delay.assert_called_once_with(photo.pk)
