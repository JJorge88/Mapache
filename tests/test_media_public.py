import pytest
from django.urls import reverse

from apps.galleries.models import Photo
from apps.galleries.services import add_photo, publish_gallery
from apps.media_processing.services import process_photo_image
from tests.factories import make_gallery, make_user
from tests.image_helpers import make_test_image

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def temporary_media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def test_public_gallery_only_renders_ready_optimized_photos(client):
    user = make_user()
    gallery = publish_gallery(gallery=make_gallery(user), published_by=user)
    ready = add_photo(
        gallery=gallery,
        original_file=make_test_image("ready.jpg"),
        uploaded_by=user,
    )
    ready = process_photo_image(ready.pk)
    pending = add_photo(
        gallery=gallery,
        original_file=make_test_image("pending.jpg"),
        uploaded_by=user,
    )
    error = add_photo(
        gallery=gallery,
        original_file=make_test_image("error.jpg"),
        uploaded_by=user,
    )
    error.processing_status = Photo.ProcessingStatus.ERROR
    error.save(update_fields=["processing_status"])

    response = client.get(reverse("galleries_public:detail", args=[gallery.slug]))
    content = response.content.decode()

    assert reverse("core_media:local_photo", args=[ready.uuid, "optimized"]) in content
    assert ready.original_file.url not in content
    assert pending.original_filename not in content
    assert error.original_filename not in content
