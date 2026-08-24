import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.media_processing.services import validate_image_file
from tests.image_helpers import make_test_image


@pytest.mark.parametrize(
    ("name", "image_format", "expected_mime"),
    [
        ("photo.jpg", "JPEG", "image/jpeg"),
        ("photo.png", "PNG", "image/png"),
        ("photo.webp", "WEBP", "image/webp"),
    ],
)
def test_supported_images_are_validated_by_content(name, image_format, expected_mime):
    result = validate_image_file(make_test_image(name, image_format=image_format))

    assert result.mime_type == expected_mime
    assert result.file_size > 0


def test_text_renamed_as_jpeg_is_rejected():
    fake = SimpleUploadedFile("malicious.jpg", b"not-an-image", content_type="image/jpeg")

    with pytest.raises(ValidationError):
        validate_image_file(fake)


def test_unsupported_format_is_rejected():
    fake = SimpleUploadedFile("vector.svg", b"<svg></svg>", content_type="image/svg+xml")

    with pytest.raises(ValidationError):
        validate_image_file(fake)


def test_extension_must_match_decoded_format():
    png_named_jpeg = make_test_image("wrong.jpg", image_format="PNG")

    with pytest.raises(ValidationError):
        validate_image_file(png_named_jpeg)


def test_file_size_limit_is_configurable(settings):
    settings.MAPACHE_MAX_PHOTO_SIZE_MB = 0

    with pytest.raises(ValidationError):
        validate_image_file(make_test_image())
