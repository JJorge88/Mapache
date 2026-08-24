import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError

from apps.galleries.models import Gallery, Photo
from tests.factories import make_gallery, make_user

pytestmark = pytest.mark.django_db


def test_gallery_defaults_and_uuid():
    user = make_user()
    first = Gallery.objects.create(title="Primera", slug="primera", created_by=user)
    second = Gallery.objects.create(title="Segunda", slug="segunda", created_by=user)

    assert first.status == Gallery.Status.DRAFT
    assert first.visibility == Gallery.Visibility.PUBLIC
    assert first.uuid != second.uuid


def test_gallery_slug_is_unique():
    user = make_user()
    Gallery.objects.create(title="Primera", slug="unica", created_by=user)

    with pytest.raises(IntegrityError):
        Gallery.objects.create(title="Segunda", slug="unica", created_by=user)


def test_photo_belongs_to_gallery_and_uses_expected_defaults():
    user = make_user()
    gallery = make_gallery(user)
    photo = Photo.objects.create(
        gallery=gallery,
        original_file=SimpleUploadedFile("one.jpg", b"one"),
        filename="one.jpg",
        original_filename="one.jpg",
        uploaded_by=user,
    )

    assert photo.gallery == gallery
    assert photo.orientation == Photo.Orientation.UNKNOWN
    assert photo.processing_status == Photo.ProcessingStatus.PENDING


def test_photo_default_ordering():
    user = make_user()
    gallery = make_gallery(user)
    second = Photo.objects.create(
        gallery=gallery,
        original_file="galleries/second.jpg",
        filename="second.jpg",
        original_filename="second.jpg",
        sort_order=8,
        uploaded_by=user,
    )
    first = Photo.objects.create(
        gallery=gallery,
        original_file="galleries/first.jpg",
        filename="first.jpg",
        original_filename="first.jpg",
        sort_order=2,
        uploaded_by=user,
    )

    assert list(gallery.photos.values_list("pk", flat=True)) == [first.pk, second.pk]


@pytest.mark.parametrize("pin", ["123", "123456789", "12a4", "", 1234])
def test_invalid_pin_is_rejected(pin):
    gallery = Gallery(title="Privada", slug="privada", created_by=make_user())

    with pytest.raises(ValidationError):
        gallery.set_pin(pin)


def test_pin_is_hashed_and_zero_prefix_is_preserved():
    gallery = Gallery(title="Privada", slug="privada", created_by=make_user())
    gallery.set_pin("0421")

    assert gallery.pin_hash != "0421"
    assert "0421" not in gallery.pin_hash
    assert gallery.check_pin("0421") is True
    assert gallery.check_pin("421") is False
