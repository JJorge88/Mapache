from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image


def make_test_image(
    name="photo.jpg",
    *,
    size=(80, 50),
    image_format="JPEG",
    color=(180, 80, 30),
    exif_orientation=None,
):
    output = BytesIO()
    image = Image.new("RGB", size, color)
    save_options = {}
    if exif_orientation is not None:
        exif = Image.Exif()
        exif[274] = exif_orientation
        save_options["exif"] = exif
    image.save(output, format=image_format, **save_options)
    image.close()
    mime = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }[image_format]
    return SimpleUploadedFile(name, output.getvalue(), content_type=mime)
