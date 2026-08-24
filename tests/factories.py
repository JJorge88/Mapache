from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import User
from apps.galleries.services import add_photo, create_gallery


def make_user(username="staff"):
    return User.objects.create_user(username=username, password="segura-123")


def make_gallery(user, title="Rodeo Chiquimula", **kwargs):
    return create_gallery(created_by=user, title=title, **kwargs)


def make_photo(gallery, user, name="photo.jpg", content=b"image-data"):
    return add_photo(
        gallery=gallery,
        original_file=SimpleUploadedFile(name, content, content_type="image/jpeg"),
        uploaded_by=user,
    )
