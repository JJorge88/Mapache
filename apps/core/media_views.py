import logging

from django.conf import settings
from django.core import signing
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404

from apps.galleries.models import Gallery, Photo

from .media_delivery import PRIVATE_MEDIA_SALT, PhotoVariant, has_private_gallery_access

logger = logging.getLogger("mapache.storage")


def _photo_field(photo, variant):
    return {
        PhotoVariant.THUMBNAIL: photo.thumbnail_file,
        PhotoVariant.OPTIMIZED: photo.optimized_file,
        PhotoVariant.ORIGINAL: photo.original_file,
    }[variant]


def _file_response(photo, variant):
    field_file = _photo_field(photo, variant)
    if not field_file or not field_file.name:
        raise Http404
    try:
        source = field_file.storage.open(field_file.name, "rb")
    except Exception as exc:
        logger.exception(
            "Media read failed gallery=%s photo=%s variant=%s",
            photo.gallery.uuid,
            photo.uuid,
            variant.value,
        )
        raise Http404 from exc
    content_type = photo.mime_type if variant == PhotoVariant.ORIGINAL else "image/webp"
    return FileResponse(
        source,
        content_type=content_type or "application/octet-stream",
        as_attachment=False,
        filename=field_file.name.rsplit("/", 1)[-1],
    )


def local_photo_media(request: HttpRequest, photo_uuid, variant: str) -> HttpResponse:
    if settings.STORAGE_BACKEND != "local":
        raise Http404
    try:
        selected_variant = PhotoVariant(variant)
    except ValueError as exc:
        raise Http404 from exc
    photo = get_object_or_404(Photo.objects.select_related("gallery"), uuid=photo_uuid)
    if request.user.is_authenticated:
        return _file_response(photo, selected_variant)
    gallery = photo.gallery
    if gallery.status != Gallery.Status.PUBLISHED or selected_variant == PhotoVariant.ORIGINAL:
        raise Http404
    if gallery.visibility == Gallery.Visibility.PRIVATE_PIN:
        raise Http404
    return _file_response(photo, selected_variant)


def private_photo_media(
    request: HttpRequest, slug: str, photo_uuid, variant: str, token: str
) -> HttpResponse:
    if settings.STORAGE_BACKEND != "local":
        raise Http404
    gallery = get_object_or_404(
        Gallery,
        slug=slug,
        status=Gallery.Status.PUBLISHED,
        visibility=Gallery.Visibility.PRIVATE_PIN,
    )
    if not has_private_gallery_access(request, gallery):
        raise Http404
    try:
        selected_variant = PhotoVariant(variant)
        if selected_variant == PhotoVariant.ORIGINAL:
            raise Http404
        signed_data = signing.loads(
            token,
            salt=PRIVATE_MEDIA_SALT,
            max_age=settings.MAPACHE_PRIVATE_MEDIA_URL_TTL,
        )
    except (ValueError, signing.BadSignature, signing.SignatureExpired) as exc:
        raise Http404 from exc
    if signed_data != {"photo": str(photo_uuid), "variant": selected_variant.value}:
        raise Http404
    photo = get_object_or_404(Photo, uuid=photo_uuid, gallery=gallery)
    return _file_response(photo, selected_variant)
