import json

import pytest
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.galleries.models import Gallery, Photo
from tests.factories import make_gallery, make_photo, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def phase4_settings(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.PUBLIC_SITE_URL = "https://studio.example"


def mark_ready(photo: Photo) -> Photo:
    photo.processing_status = Photo.ProcessingStatus.READY
    photo.optimized_file.name = f"optimized/{photo.uuid}.webp"
    photo.thumbnail_file.name = f"thumbnails/{photo.uuid}.webp"
    photo.save(update_fields=["processing_status", "optimized_file", "thumbnail_file"])
    return photo


def test_photo_workspace_paginates_at_60_and_never_renders_original_url(client):
    user = make_user()
    gallery = make_gallery(user)
    first = mark_ready(make_photo(gallery, user, "private-original.jpg"))
    for index in range(60):
        make_photo(gallery, user, f"photo-{index}.jpg")
    client.force_login(user)

    first_page = client.get(reverse("galleries_dashboard:photos", args=[gallery.uuid]))
    second_page = client.get(
        reverse("galleries_dashboard:photos", args=[gallery.uuid]), {"page": 2}
    )

    assert first_page.status_code == 200
    assert len(first_page.context["photos"]) == 60
    assert len(second_page.context["photos"]) == 1
    assert first.original_file.url not in first_page.content.decode()
    assert "Selección aplicada solo a esta página" in first_page.content.decode()
    assert "Elegir portada" in first_page.content.decode()
    assert "data-preview-cover" in first_page.content.decode()


def test_cover_endpoint_accepts_only_ready_photos(client):
    user = make_user()
    gallery = make_gallery(user)
    pending = make_photo(gallery, user, "pending.jpg")
    ready = mark_ready(make_photo(gallery, user, "ready.jpg"))
    client.force_login(user)

    client.post(reverse("galleries_dashboard:photo_cover", args=[gallery.uuid, pending.uuid]))
    gallery.refresh_from_db()
    assert gallery.cover_photo is None

    response = client.post(
        reverse("galleries_dashboard:photo_cover", args=[gallery.uuid, ready.uuid])
    )
    gallery.refresh_from_db()
    assert response.status_code == 302
    assert gallery.cover_photo == ready


def test_bulk_delete_removes_selection_clears_cover_and_audits(client):
    user = make_user()
    gallery = make_gallery(user)
    first = mark_ready(make_photo(gallery, user, "first.jpg"))
    second = make_photo(gallery, user, "second.jpg")
    gallery.cover_photo = first
    gallery.save(update_fields=["cover_photo"])
    client.force_login(user)

    response = client.post(
        reverse("galleries_dashboard:photos_bulk_delete", args=[gallery.uuid]),
        {"photo_uuids": [str(first.uuid), str(second.uuid)]},
    )
    gallery.refresh_from_db()

    assert response.status_code == 302
    assert gallery.photos.count() == 0
    assert gallery.cover_photo is None
    assert AuditLog.objects.filter(
        action="PHOTOS_BULK_DELETED", object_id=str(gallery.uuid)
    ).exists()


def test_reorder_endpoint_requires_the_complete_current_uuid_set(client):
    user = make_user()
    gallery = make_gallery(user)
    first = make_photo(gallery, user, "first.jpg")
    second = make_photo(gallery, user, "second.jpg")
    url = reverse("galleries_dashboard:photos_reorder", args=[gallery.uuid])
    client.force_login(user)

    response = client.post(
        url,
        data=json.dumps({"photo_uuids": [str(second.uuid), str(first.uuid)]}),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert list(gallery.photos.order_by("sort_order").values_list("uuid", flat=True)) == [
        second.uuid,
        first.uuid,
    ]

    conflict = client.post(
        url,
        data=json.dumps({"photo_uuids": [str(first.uuid)]}),
        content_type="application/json",
    )
    assert conflict.status_code == 409


def test_share_and_qr_use_public_site_url_without_exposing_pin(client):
    user = make_user()
    gallery = make_gallery(user, visibility=Gallery.Visibility.PRIVATE_PIN)
    gallery.set_pin("4821")
    gallery.save(update_fields=["pin_hash"])
    client.force_login(user)

    share = client.get(reverse("galleries_dashboard:share", args=[gallery.uuid]))
    qr = client.get(reverse("galleries_dashboard:qr", args=[gallery.uuid]))

    expected = f"https://studio.example/g/{gallery.slug}/"
    assert share.json()["url"] == expected
    assert share.json()["pin_configured"] is True
    assert "4821" not in share.content.decode()
    assert qr.status_code == 200
    assert qr["Content-Type"] == "image/png"
    assert qr.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_publish_with_processing_photos_is_allowed_and_opens_share_success(client):
    user = make_user()
    gallery = make_gallery(user)
    make_photo(gallery, user, "pending.jpg")
    client.force_login(user)

    response = client.post(reverse("galleries_dashboard:publish", args=[gallery.uuid]))

    gallery.refresh_from_db()
    assert gallery.status == Gallery.Status.PUBLISHED
    assert response.url.endswith("?published=1")
    detail = client.get(response.url)
    assert "data-modal-autopen" in detail.content.decode()


def test_phase4_endpoints_are_authenticated(client):
    user = make_user()
    gallery = make_gallery(user)
    photo = make_photo(gallery, user)
    targets = [
        ("post", reverse("galleries_dashboard:photos_bulk_delete", args=[gallery.uuid])),
        ("post", reverse("galleries_dashboard:photos_reorder", args=[gallery.uuid])),
        (
            "post",
            reverse("galleries_dashboard:photo_cover", args=[gallery.uuid, photo.uuid]),
        ),
        ("get", reverse("galleries_dashboard:share", args=[gallery.uuid])),
        ("get", reverse("galleries_dashboard:qr", args=[gallery.uuid])),
    ]

    for method, url in targets:
        response = getattr(client, method)(url)
        assert response.status_code == 302
        assert reverse("accounts:login") in response.url
