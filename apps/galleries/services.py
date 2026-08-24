import logging
from collections.abc import Iterable
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.text import slugify

from apps.audit.models import AuditLog

from .models import Gallery, Photo

storage_logger = logging.getLogger("mapache.storage")


def _audit(*, user, action: str, gallery: Gallery, metadata: dict | None = None) -> None:
    AuditLog.objects.create(
        user=user,
        action=action,
        model_name="Gallery",
        object_id=str(gallery.uuid),
        metadata=metadata or {},
    )


def _available_slug(title: str, requested_slug: str = "") -> str:
    base = slugify(requested_slug or title)[:200] or "galeria"
    candidate = base
    suffix = 2
    while Gallery.objects.filter(slug=candidate).exists():
        ending = f"-{suffix}"
        candidate = f"{base[: 220 - len(ending)]}{ending}"
        suffix += 1
    return candidate


@transaction.atomic
def create_gallery(*, created_by, title: str, slug: str = "", **data) -> Gallery:
    gallery = Gallery(
        created_by=created_by,
        title=title,
        slug=_available_slug(title, slug),
        **data,
    )
    gallery.full_clean()
    try:
        gallery.save()
    except IntegrityError as exc:
        raise ValidationError({"slug": "No fue posible generar un slug único."}) from exc
    _audit(
        user=created_by,
        action="GALLERY_CREATED",
        gallery=gallery,
        metadata={"title": gallery.title},
    )
    return gallery


@transaction.atomic
def update_gallery(*, gallery: Gallery, updated_by, **changes) -> Gallery:
    allowed = {
        "title",
        "description",
        "event_date",
        "show_in_portfolio",
        "is_featured",
        "allow_photo_download",
        "allow_gallery_download",
    }
    invalid = set(changes) - allowed
    if invalid:
        raise ValidationError(f"Campos no editables: {', '.join(sorted(invalid))}")
    for field, value in changes.items():
        setattr(gallery, field, value)
    gallery.full_clean()
    gallery.save(update_fields=[*changes, "updated_at"])
    _audit(
        user=updated_by,
        action="GALLERY_UPDATED",
        gallery=gallery,
        metadata={"fields": sorted(changes)},
    )
    return gallery


@transaction.atomic
def publish_gallery(*, gallery: Gallery, published_by) -> Gallery:
    gallery = Gallery.objects.select_for_update().get(pk=gallery.pk)
    if gallery.status == Gallery.Status.ARCHIVED:
        raise ValidationError("Una galería archivada no puede publicarse directamente.")
    if gallery.visibility == Gallery.Visibility.PRIVATE_PIN and not gallery.has_pin:
        raise ValidationError("La galería privada necesita un PIN antes de publicarse.")
    gallery.status = Gallery.Status.PUBLISHED
    if gallery.published_at is None:
        gallery.published_at = timezone.now()
    gallery.full_clean()
    gallery.save(update_fields=["status", "published_at", "updated_at"])
    _audit(user=published_by, action="GALLERY_PUBLISHED", gallery=gallery)
    return gallery


@transaction.atomic
def archive_gallery(*, gallery: Gallery, archived_by) -> Gallery:
    gallery = Gallery.objects.select_for_update().get(pk=gallery.pk)
    gallery.status = Gallery.Status.ARCHIVED
    gallery.save(update_fields=["status", "updated_at"])
    _audit(user=archived_by, action="GALLERY_ARCHIVED", gallery=gallery)
    return gallery


@transaction.atomic
def change_gallery_visibility(*, gallery: Gallery, visibility: str, changed_by) -> Gallery:
    if visibility not in Gallery.Visibility.values:
        raise ValidationError({"visibility": "Visibilidad inválida."})
    if (
        gallery.status == Gallery.Status.PUBLISHED
        and visibility == Gallery.Visibility.PRIVATE_PIN
        and not gallery.has_pin
    ):
        raise ValidationError("Configura un PIN antes de hacer privada una galería publicada.")
    previous = gallery.visibility
    gallery.visibility = visibility
    if previous == Gallery.Visibility.PRIVATE_PIN and visibility != previous:
        gallery.pin_hash = ""
    gallery.full_clean()
    gallery.save(update_fields=["visibility", "pin_hash", "updated_at"])
    _audit(
        user=changed_by,
        action="GALLERY_VISIBILITY_CHANGED",
        gallery=gallery,
        metadata={"from": previous, "to": visibility},
    )
    return gallery


@transaction.atomic
def change_gallery_pin(*, gallery: Gallery, pin: str, changed_by) -> Gallery:
    gallery.set_pin(pin)
    gallery.save(update_fields=["pin_hash", "updated_at"])
    _audit(user=changed_by, action="GALLERY_PIN_CHANGED", gallery=gallery)
    return gallery


@transaction.atomic
def set_gallery_cover(*, gallery: Gallery, photo: Photo | None, changed_by) -> Gallery:
    if photo is not None and photo.gallery_id != gallery.id:
        raise ValidationError("La fotografía no pertenece a esta galería.")
    if photo is not None and photo.processing_status != Photo.ProcessingStatus.READY:
        raise ValidationError("Solo una fotografía lista puede utilizarse como portada.")
    gallery.cover_photo = photo
    gallery.save(update_fields=["cover_photo", "updated_at"])
    _audit(
        user=changed_by,
        action="GALLERY_COVER_CHANGED",
        gallery=gallery,
        metadata={"photo_uuid": str(photo.uuid) if photo else None},
    )
    return gallery


@transaction.atomic
def add_photo(
    *,
    gallery: Gallery,
    original_file,
    uploaded_by,
    filename: str = "",
    original_filename: str = "",
    mime_type: str = "",
    file_size: int | None = None,
    width: int | None = None,
    height: int | None = None,
    photo_uuid=None,
) -> Photo:
    Gallery.objects.select_for_update().get(pk=gallery.pk)
    last_order = gallery.photos.aggregate(last=Max("sort_order"))["last"]
    uploaded_name = Path(getattr(original_file, "name", "photo")).name
    photo = Photo(
        **({"uuid": photo_uuid} if photo_uuid is not None else {}),
        gallery=gallery,
        original_file=original_file,
        filename=filename or uploaded_name,
        original_filename=original_filename or uploaded_name,
        mime_type=mime_type or getattr(original_file, "content_type", ""),
        file_size=file_size if file_size is not None else getattr(original_file, "size", None),
        width=width,
        height=height,
        sort_order=(last_order + 1) if last_order is not None else 0,
        uploaded_by=uploaded_by,
    )
    photo.full_clean()
    try:
        photo.save()
    except Exception:
        storage_logger.exception(
            "Photo upload failed gallery=%s filename=%s", gallery.uuid, uploaded_name
        )
        raise
    _audit(
        user=uploaded_by,
        action="PHOTO_ADDED",
        gallery=gallery,
        metadata={"photo_uuid": str(photo.uuid), "filename": photo.filename},
    )
    return photo


@transaction.atomic
def delete_photo(*, photo: Photo, deleted_by) -> None:
    gallery = photo.gallery
    photo_uuid = str(photo.uuid)
    if gallery.cover_photo_id == photo.id:
        gallery.cover_photo = None
        gallery.save(update_fields=["cover_photo", "updated_at"])
    stored_files = [
        (field.storage, field.name)
        for field in (photo.original_file, photo.optimized_file, photo.thumbnail_file)
        if field.name
    ]
    photo.delete()
    for storage, stored_name in stored_files:
        try:
            storage.delete(stored_name)
        except Exception:
            storage_logger.exception(
                "Photo object delete failed gallery=%s photo=%s name=%s",
                gallery.uuid,
                photo_uuid,
                stored_name,
            )
            raise
    _audit(
        user=deleted_by,
        action="PHOTO_DELETED",
        gallery=gallery,
        metadata={"photo_uuid": photo_uuid},
    )


@transaction.atomic
def delete_photos(*, gallery: Gallery, photo_uuids: Iterable, deleted_by) -> int:
    requested = [str(value) for value in photo_uuids]
    if not requested:
        raise ValidationError("Selecciona al menos una fotografía.")
    if len(requested) != len(set(requested)):
        raise ValidationError("La selección contiene fotografías duplicadas.")
    photos = list(Photo.objects.select_for_update().filter(gallery=gallery, uuid__in=requested))
    if len(photos) != len(requested):
        raise ValidationError("Una o más fotografías no pertenecen a esta galería.")
    for photo in photos:
        delete_photo(photo=photo, deleted_by=deleted_by)
    _audit(
        user=deleted_by,
        action="PHOTOS_BULK_DELETED",
        gallery=gallery,
        metadata={"count": len(photos)},
    )
    return len(photos)


@transaction.atomic
def reorder_photos(*, gallery: Gallery, photo_uuids: Iterable, reordered_by) -> list[Photo]:
    requested = [str(value) for value in photo_uuids]
    if len(requested) != len(set(requested)):
        raise ValidationError("La lista contiene fotografías duplicadas.")
    photos = list(Photo.objects.select_for_update().filter(gallery=gallery))
    by_uuid = {str(photo.uuid): photo for photo in photos}
    if set(requested) != set(by_uuid):
        raise ValidationError("La lista debe contener exactamente las fotografías de la galería.")
    ordered = []
    for position, photo_uuid in enumerate(requested):
        photo = by_uuid[photo_uuid]
        photo.sort_order = position
        ordered.append(photo)
    Photo.objects.bulk_update(ordered, ["sort_order"])
    _audit(
        user=reordered_by,
        action="PHOTOS_REORDERED",
        gallery=gallery,
        metadata={"count": len(ordered)},
    )
    return ordered
