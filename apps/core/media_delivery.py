import logging
from enum import StrEnum

from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.galleries.models import Gallery, Photo

logger = logging.getLogger("mapache.storage")
PRIVATE_MEDIA_SALT = "mapache.private-media.v1"


class PhotoVariant(StrEnum):
    THUMBNAIL = "thumbnail"
    OPTIMIZED = "optimized"
    ORIGINAL = "original"


def _field_for_variant(photo: Photo, variant: PhotoVariant):
    return {
        PhotoVariant.THUMBNAIL: photo.thumbnail_file,
        PhotoVariant.OPTIMIZED: photo.optimized_file,
        PhotoVariant.ORIGINAL: photo.original_file,
    }[variant]


def has_private_gallery_access(request, gallery: Gallery) -> bool:
    return bool(request and request.session.get(f"gallery_access_{gallery.uuid}", False))


def _authorize_public(request, photo: Photo, variant: PhotoVariant) -> None:
    gallery = photo.gallery
    if gallery.status != Gallery.Status.PUBLISHED:
        raise PermissionDenied("La galería no está publicada.")
    if variant == PhotoVariant.ORIGINAL:
        raise PermissionDenied("Los originales no tienen entrega pública.")
    if gallery.visibility == Gallery.Visibility.PRIVATE_PIN and not has_private_gallery_access(
        request, gallery
    ):
        raise PermissionDenied("La galería requiere acceso mediante PIN.")


def _authorize_dashboard(request) -> None:
    if not request or not request.user.is_authenticated:
        raise PermissionDenied("Se requiere acceso administrativo.")


def get_photo_delivery_url(
    *,
    photo: Photo,
    variant: str | PhotoVariant,
    request,
    audience: str = "public",
    allow_original: bool = False,
) -> str:
    variant = PhotoVariant(variant)
    if variant == PhotoVariant.ORIGINAL and not allow_original:
        raise PermissionDenied("La entrega de originales requiere autorización explícita.")
    if audience == "public":
        _authorize_public(request, photo, variant)
    elif audience == "dashboard":
        _authorize_dashboard(request)
    else:
        raise ValueError("Audiencia de media desconocida.")
    field_file = _field_for_variant(photo, variant)
    if not field_file or not field_file.name:
        return ""
    try:
        if settings.STORAGE_BACKEND == "r2":
            ttl = (
                settings.MAPACHE_PRIVATE_MEDIA_URL_TTL
                if photo.gallery.visibility == Gallery.Visibility.PRIVATE_PIN
                or audience == "dashboard"
                else settings.MAPACHE_PUBLIC_MEDIA_URL_TTL
            )
            return field_file.storage.url(field_file.name, expire=ttl)
        if audience == "public" and photo.gallery.visibility == Gallery.Visibility.PRIVATE_PIN:
            token = signing.dumps(
                {"photo": str(photo.uuid), "variant": variant.value},
                salt=PRIVATE_MEDIA_SALT,
                compress=True,
            )
            return reverse(
                "core_media:private_photo",
                args=[photo.gallery.slug, photo.uuid, variant.value, token],
            )
        return reverse("core_media:local_photo", args=[photo.uuid, variant.value])
    except Exception:
        logger.exception(
            "Media delivery URL failed gallery=%s photo=%s variant=%s",
            photo.gallery.uuid,
            photo.uuid,
            variant.value,
        )
        return ""


def get_thumbnail_url(*, photo: Photo, request, audience: str = "public") -> str:
    return get_photo_delivery_url(
        photo=photo, variant=PhotoVariant.THUMBNAIL, request=request, audience=audience
    )


def get_optimized_url(*, photo: Photo, request, audience: str = "public") -> str:
    return get_photo_delivery_url(
        photo=photo, variant=PhotoVariant.OPTIMIZED, request=request, audience=audience
    )


def get_original_download_url(*, photo: Photo, request) -> str:
    return get_photo_delivery_url(
        photo=photo,
        variant=PhotoVariant.ORIGINAL,
        request=request,
        audience="dashboard",
        allow_original=True,
    )
