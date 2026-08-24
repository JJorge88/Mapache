from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.galleries.models import Photo
from apps.galleries.services import add_photo, delete_photo
from apps.media_processing.exceptions import PermanentImageError
from apps.media_processing.services import process_photo_image, reprocess_photo
from apps.media_processing.tasks import process_photo
from tests.factories import make_gallery, make_user
from tests.image_helpers import make_test_image

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def temporary_media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.MAPACHE_OPTIMIZED_MAX_DIMENSION = 120
    settings.MAPACHE_THUMBNAIL_MAX_DIMENSION = 40
    settings.MAPACHE_IMAGE_WEBP_QUALITY = 86


def make_unprocessed_photo(gallery, user, image=None):
    return add_photo(
        gallery=gallery,
        original_file=image or make_test_image(),
        uploaded_by=user,
    )


def open_image(field_file):
    with field_file.storage.open(field_file.name, "rb") as source:
        image = Image.open(source)
        image.load()
        return image.copy(), image.format


def test_processing_generates_webp_derivatives_and_preserves_original():
    user = make_user()
    gallery = make_gallery(user)
    photo = make_unprocessed_photo(
        gallery,
        user,
        make_test_image(size=(320, 160)),
    )
    with photo.original_file.storage.open(photo.original_file.name, "rb") as source:
        original_before = source.read()

    processed = process_photo_image(photo.pk)
    with processed.original_file.storage.open(processed.original_file.name, "rb") as source:
        original_after = source.read()
    optimized, optimized_format = open_image(processed.optimized_file)
    thumbnail, thumbnail_format = open_image(processed.thumbnail_file)

    assert processed.processing_status == Photo.ProcessingStatus.READY
    assert processed.processed_at is not None
    assert original_after == original_before
    assert optimized_format == thumbnail_format == "WEBP"
    assert max(optimized.size) <= 120
    assert max(thumbnail.size) <= 40
    assert optimized.width / optimized.height == pytest.approx(2.0)
    assert thumbnail.width / thumbnail.height == pytest.approx(2.0)
    assert processed.width == 320
    assert processed.height == 160
    optimized.close()
    thumbnail.close()


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((100, 50), Photo.Orientation.LANDSCAPE),
        ((50, 100), Photo.Orientation.PORTRAIT),
        ((80, 80), Photo.Orientation.SQUARE),
    ],
)
def test_orientation_is_detected_after_processing(size, expected):
    user = make_user()
    photo = make_unprocessed_photo(make_gallery(user), user, make_test_image(size=size))

    processed = process_photo_image(photo.pk)

    assert processed.orientation == expected
    assert (processed.width, processed.height) == size


def test_exif_orientation_is_normalized_before_dimensions():
    user = make_user()
    rotated = make_test_image(size=(90, 40), exif_orientation=6)
    photo = make_unprocessed_photo(make_gallery(user), user, rotated)

    processed = process_photo_image(photo.pk)

    assert (processed.width, processed.height) == (40, 90)
    assert processed.orientation == Photo.Orientation.PORTRAIT


def test_small_images_are_not_upscaled():
    user = make_user()
    photo = make_unprocessed_photo(make_gallery(user), user, make_test_image(size=(20, 10)))

    processed = process_photo_image(photo.pk)
    optimized, _ = open_image(processed.optimized_file)
    thumbnail, _ = open_image(processed.thumbnail_file)

    assert optimized.size == (20, 10)
    assert thumbnail.size == (20, 10)
    optimized.close()
    thumbnail.close()


def test_internal_names_use_uuid_not_user_filename():
    user = make_user()
    photo = make_unprocessed_photo(
        make_gallery(user),
        user,
        make_test_image("My Vacation Final.JPG"),
    )
    processed = process_photo_image(photo.pk)

    assert Path(processed.original_file.name).name == f"{processed.uuid}.jpg"
    assert Path(processed.optimized_file.name).name == f"{processed.uuid}.webp"
    assert Path(processed.thumbnail_file.name).name == f"{processed.uuid}.webp"
    assert processed.original_filename == "My Vacation Final.JPG"


def test_processing_is_idempotent_and_reuses_derivative_names():
    user = make_user()
    photo = make_unprocessed_photo(make_gallery(user), user)
    first = process_photo_image(photo.pk)
    names = (first.optimized_file.name, first.thumbnail_file.name)

    second = process_photo_image(photo.pk)

    assert (second.optimized_file.name, second.thumbnail_file.name) == names
    for name in names:
        directory, filename = name.rsplit("/", 1)
        assert second.optimized_file.storage.listdir(directory)[1].count(filename) == 1


def test_corrupt_image_moves_to_error_without_ready_state():
    user = make_user()
    corrupt = SimpleUploadedFile("broken.jpg", b"broken")
    photo = make_unprocessed_photo(make_gallery(user), user, corrupt)

    result = process_photo.apply(args=[photo.pk]).get()
    photo.refresh_from_db()

    assert result["status"] == "error"
    assert photo.processing_status == Photo.ProcessingStatus.ERROR
    assert photo.processing_error
    assert not photo.optimized_file
    assert not photo.thumbnail_file


def test_celery_task_processes_valid_photo_in_eager_mode():
    user = make_user()
    photo = make_unprocessed_photo(make_gallery(user), user)

    result = process_photo.apply(args=[photo.pk]).get()
    photo.refresh_from_db()

    assert result["status"] == Photo.ProcessingStatus.READY
    assert photo.processing_status == Photo.ProcessingStatus.READY


def test_celery_task_ignores_deleted_photo():
    result = process_photo.apply(args=[999999]).get()

    assert result == {"photo_id": 999999, "status": "missing"}


def test_manual_retry_can_recover_after_original_is_replaced():
    user = make_user()
    corrupt = SimpleUploadedFile("broken.jpg", b"broken")
    photo = make_unprocessed_photo(make_gallery(user), user, corrupt)
    with pytest.raises(PermanentImageError):
        process_photo_image(photo.pk)
    original_name = photo.original_file.name
    storage = photo.original_file.storage
    storage.delete(original_name)
    valid = make_test_image()
    storage.save(original_name, valid)

    with patch("apps.media_processing.tasks.process_photo.delay"):
        reprocess_photo(photo=photo, requested_by=user)
    recovered = process_photo_image(photo.pk)

    assert recovered.processing_status == Photo.ProcessingStatus.READY
    assert recovered.processing_error == ""


def test_delete_removes_original_and_both_derivatives_and_cover():
    user = make_user()
    gallery = make_gallery(user)
    photo = process_photo_image(make_unprocessed_photo(gallery, user).pk)
    gallery.cover_photo = photo
    gallery.save(update_fields=["cover_photo"])
    files = [
        (field.storage, field.name)
        for field in (photo.original_file, photo.optimized_file, photo.thumbnail_file)
    ]

    delete_photo(photo=photo, deleted_by=user)
    gallery.refresh_from_db()

    assert gallery.cover_photo is None
    assert all(not storage.exists(name) for storage, name in files)


def test_delete_does_not_fail_when_a_derivative_is_already_missing():
    user = make_user()
    photo = process_photo_image(make_unprocessed_photo(make_gallery(user), user).pk)
    photo.thumbnail_file.storage.delete(photo.thumbnail_file.name)

    delete_photo(photo=photo, deleted_by=user)

    assert not Photo.objects.filter(uuid=photo.uuid).exists()
