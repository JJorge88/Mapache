import pytest
from django.core.exceptions import ValidationError

from apps.audit.models import AuditLog
from apps.galleries.models import Gallery, Photo
from apps.galleries.services import (
    archive_gallery,
    change_gallery_pin,
    change_gallery_visibility,
    delete_photo,
    publish_gallery,
    reorder_photos,
    set_gallery_cover,
    update_gallery,
)
from tests.factories import make_gallery, make_photo, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def temporary_media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def test_create_gallery_generates_slug_and_audit():
    user = make_user()
    gallery = make_gallery(user, title="Rodeo Chiquimula")

    assert gallery.slug == "rodeo-chiquimula"
    assert AuditLog.objects.filter(
        action="GALLERY_CREATED", object_id=str(gallery.uuid), user=user
    ).exists()


def test_slug_collision_gets_numeric_suffix():
    user = make_user()
    make_gallery(user, title="Rodeo")
    second = make_gallery(user, title="Rodeo")

    assert second.slug == "rodeo-2"


def test_update_gallery_keeps_slug_stable():
    user = make_user()
    gallery = make_gallery(user, title="Nombre inicial")

    update_gallery(gallery=gallery, updated_by=user, title="Nombre final")

    assert gallery.title == "Nombre final"
    assert gallery.slug == "nombre-inicial"


def test_publish_public_gallery_sets_published_at_only_once():
    user = make_user()
    gallery = make_gallery(user)
    published = publish_gallery(gallery=gallery, published_by=user)
    first_timestamp = published.published_at
    published = publish_gallery(gallery=published, published_by=user)

    assert published.status == Gallery.Status.PUBLISHED
    assert published.published_at == first_timestamp


def test_private_gallery_without_pin_cannot_publish():
    user = make_user()
    gallery = make_gallery(user, visibility=Gallery.Visibility.PRIVATE_PIN)

    with pytest.raises(ValidationError):
        publish_gallery(gallery=gallery, published_by=user)


def test_private_gallery_with_pin_can_publish():
    user = make_user()
    gallery = make_gallery(user, visibility=Gallery.Visibility.PRIVATE_PIN)
    change_gallery_pin(gallery=gallery, pin="0421", changed_by=user)

    published = publish_gallery(gallery=gallery, published_by=user)

    assert published.status == Gallery.Status.PUBLISHED


def test_archive_preserves_photos():
    user = make_user()
    gallery = make_gallery(user)
    make_photo(gallery, user)

    archived = archive_gallery(gallery=gallery, archived_by=user)

    assert archived.status == Gallery.Status.ARCHIVED
    assert archived.photos.count() == 1


def test_archived_gallery_cannot_publish():
    user = make_user()
    gallery = archive_gallery(gallery=make_gallery(user), archived_by=user)

    with pytest.raises(ValidationError):
        publish_gallery(gallery=gallery, published_by=user)


def test_leaving_private_visibility_removes_pin_hash():
    user = make_user()
    gallery = make_gallery(user, visibility=Gallery.Visibility.PRIVATE_PIN)
    change_gallery_pin(gallery=gallery, pin="4821", changed_by=user)

    changed = change_gallery_visibility(
        gallery=gallery,
        visibility=Gallery.Visibility.PUBLIC,
        changed_by=user,
    )

    assert changed.pin_hash == ""


def test_pin_never_appears_in_audit_metadata():
    user = make_user()
    gallery = make_gallery(user)

    change_gallery_pin(gallery=gallery, pin="928174", changed_by=user)

    audit = AuditLog.objects.get(action="GALLERY_PIN_CHANGED")
    assert "928174" not in str(audit.metadata)


def test_valid_cover_is_set_and_cross_gallery_cover_is_rejected():
    user = make_user()
    gallery = make_gallery(user, title="Primera")
    other = make_gallery(user, title="Segunda")
    photo = make_photo(gallery, user, "cover.jpg")
    foreign_photo = make_photo(other, user, "foreign.jpg")
    Photo.objects.filter(pk__in=[photo.pk, foreign_photo.pk]).update(
        processing_status=Photo.ProcessingStatus.READY
    )
    photo.refresh_from_db()
    foreign_photo.refresh_from_db()

    set_gallery_cover(gallery=gallery, photo=photo, changed_by=user)
    assert gallery.cover_photo == photo
    with pytest.raises(ValidationError):
        set_gallery_cover(gallery=gallery, photo=foreign_photo, changed_by=user)


def test_add_photo_uses_max_sort_order_plus_one():
    user = make_user()
    gallery = make_gallery(user)
    first = make_photo(gallery, user, "first.jpg")
    first.sort_order = 7
    first.save(update_fields=["sort_order"])

    second = make_photo(gallery, user, "second.jpg")

    assert second.sort_order == 8


def test_delete_cover_photo_clears_cover_and_deletes_file():
    user = make_user()
    gallery = make_gallery(user)
    photo = make_photo(gallery, user, "cover.jpg")
    photo.processing_status = Photo.ProcessingStatus.READY
    photo.save(update_fields=["processing_status"])
    set_gallery_cover(gallery=gallery, photo=photo, changed_by=user)
    storage = photo.original_file.storage
    stored_name = photo.original_file.name

    delete_photo(photo=photo, deleted_by=user)
    gallery.refresh_from_db()

    assert gallery.cover_photo is None
    assert not Photo.objects.filter(pk=photo.pk).exists()
    assert storage.exists(stored_name) is False


def test_reorder_photos_and_reject_invalid_sets():
    user = make_user()
    gallery = make_gallery(user, title="Primera")
    other = make_gallery(user, title="Segunda")
    first = make_photo(gallery, user, "first.jpg")
    second = make_photo(gallery, user, "second.jpg")
    foreign = make_photo(other, user, "foreign.jpg")

    reorder_photos(
        gallery=gallery,
        photo_uuids=[second.uuid, first.uuid],
        reordered_by=user,
    )
    first.refresh_from_db()
    second.refresh_from_db()
    assert (second.sort_order, first.sort_order) == (0, 1)

    with pytest.raises(ValidationError):
        reorder_photos(
            gallery=gallery,
            photo_uuids=[first.uuid, foreign.uuid],
            reordered_by=user,
        )
    with pytest.raises(ValidationError):
        reorder_photos(
            gallery=gallery,
            photo_uuids=[first.uuid, first.uuid],
            reordered_by=user,
        )
