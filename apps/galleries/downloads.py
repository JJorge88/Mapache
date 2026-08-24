import hashlib
import json
import logging
import re
import shutil
import tempfile
import time
import zipfile
from datetime import timedelta
from pathlib import PurePath

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.audit.models import AuditLog

from .models import Gallery, GalleryDownload, Photo

logger = logging.getLogger("mapache.downloads")
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
ZIP_COPY_CHUNK_SIZE = 1024 * 1024
ZIP_PROGRESS_BATCH_SIZE = 10
TEMP_SPACE_MARGIN_BYTES = 100 * 1024 * 1024


class PermanentDownloadError(Exception):
    pass


class TransientDownloadError(Exception):
    pass


def downloadable_photos(gallery: Gallery):
    return gallery.photos.filter(
        processing_status=Photo.ProcessingStatus.READY,
        original_file__gt="",
    ).order_by("sort_order", "created_at", "id")


def gallery_content_fingerprint(gallery: Gallery) -> str:
    payload = [
        {
            "uuid": str(photo.uuid),
            "order": photo.sort_order,
            "original": photo.original_file.name,
            "filename": photo.original_filename,
            "size": photo.file_size,
            "updated": photo.updated_at.isoformat(),
        }
        for photo in downloadable_photos(gallery).only(
            "uuid",
            "sort_order",
            "original_file",
            "original_filename",
            "file_size",
            "updated_at",
            "created_at",
        )
    ]
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()


def session_authorization_hash(request, *, create: bool = False) -> str:
    if not request.session.session_key and create:
        request.session.create()
        request.session["mapache_download_session"] = True
    session_key = request.session.session_key
    if not session_key:
        return ""
    return salted_hmac(
        "mapache.gallery-download.session.v1",
        session_key,
        algorithm="sha256",
    ).hexdigest()


def sanitize_download_filename(filename: str, *, fallback: str = "foto.jpg") -> str:
    normalized = CONTROL_CHARACTERS.sub("", str(filename or "")).replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1].strip().strip(".")
    basename = basename.replace('"', "").replace("'", "")
    if not basename or basename in {".", ".."}:
        basename = fallback
    return basename[:220]


def zip_member_name(photo: Photo, position: int, total: int) -> str:
    width = max(3, len(str(total)))
    fallback_extension = PurePath(photo.original_file.name).suffix or ".jpg"
    safe_name = sanitize_download_filename(
        photo.original_filename,
        fallback=f"foto-{photo.uuid}{fallback_extension}",
    )
    return f"{position:0{width}d}_{safe_name}"


def _mark_expired(download: GalleryDownload) -> None:
    if download.file and download.file.name:
        try:
            download.file.storage.delete(download.file.name)
        except Exception:
            logger.exception(
                "Download expiration delete failed gallery=%s download=%s",
                download.gallery.uuid,
                download.uuid,
            )
    download.status = GalleryDownload.Status.EXPIRED
    download.file = ""
    download.save(update_fields=["status", "file", "updated_at"])


def expire_download(download: GalleryDownload) -> GalleryDownload:
    with transaction.atomic():
        locked = (
            GalleryDownload.objects.select_for_update()
            .select_related("gallery")
            .get(pk=download.pk)
        )
        if locked.status != GalleryDownload.Status.EXPIRED:
            _mark_expired(locked)
        return locked


def request_gallery_download(*, gallery: Gallery, request) -> tuple[GalleryDownload, bool]:
    authorization_hash = session_authorization_hash(request, create=True)
    now = timezone.now()
    with transaction.atomic():
        locked_gallery = Gallery.objects.select_for_update().get(pk=gallery.pk)
        fingerprint = gallery_content_fingerprint(locked_gallery)
        photos = downloadable_photos(locked_gallery)
        photo_count = photos.count()
        if photo_count == 0:
            raise PermanentDownloadError("La galería no contiene fotografías listas.")
        if photo_count > settings.MAPACHE_GALLERY_DOWNLOAD_MAX_PHOTOS:
            raise PermanentDownloadError("La galería supera el máximo de fotografías permitido.")

        expired_matches = GalleryDownload.objects.select_for_update().filter(
            gallery=locked_gallery,
            authorization_hash=authorization_hash,
            content_fingerprint=fingerprint,
            status=GalleryDownload.Status.READY,
            expires_at__lte=now,
        )
        for expired in expired_matches:
            _mark_expired(expired)

        reusable = (
            GalleryDownload.objects.select_for_update()
            .filter(
                gallery=locked_gallery,
                authorization_hash=authorization_hash,
                content_fingerprint=fingerprint,
                status=GalleryDownload.Status.READY,
                expires_at__gt=now,
            )
            .first()
        )
        if reusable:
            if reusable.file and reusable.file.storage.exists(reusable.file.name):
                return reusable, False
            reusable.status = GalleryDownload.Status.ERROR
            reusable.error = "El archivo preparado ya no está disponible."
            reusable.save(update_fields=["status", "error", "updated_at"])

        active = (
            GalleryDownload.objects.select_for_update()
            .filter(
                gallery=locked_gallery,
                authorization_hash=authorization_hash,
                status__in=[
                    GalleryDownload.Status.PENDING,
                    GalleryDownload.Status.PROCESSING,
                ],
            )
            .first()
        )
        if active and active.content_fingerprint == fingerprint:
            return active, False
        if active:
            active.status = GalleryDownload.Status.ERROR
            active.error = "El contenido de la galería cambió durante la preparación."
            active.save(update_fields=["status", "error", "updated_at"])

        download = GalleryDownload.objects.create(
            gallery=locked_gallery,
            photo_count=photo_count,
            content_fingerprint=fingerprint,
            authorization_hash=authorization_hash,
        )
        from .tasks import build_gallery_download

        transaction.on_commit(lambda: build_gallery_download.delay(download.pk))
        return download, True


def _validate_sources(photos) -> int:
    total_bytes = 0
    for photo in photos:
        field_file = photo.original_file
        try:
            if not field_file.storage.exists(field_file.name):
                raise PermanentDownloadError(f"Falta el original de la fotografía {photo.uuid}.")
            total_bytes += field_file.storage.size(field_file.name)
        except PermanentDownloadError:
            raise
        except Exception as exc:
            raise TransientDownloadError("No fue posible validar los originales.") from exc
    configured_limit = settings.MAPACHE_GALLERY_DOWNLOAD_MAX_BYTES
    if configured_limit and total_bytes > configured_limit:
        raise PermanentDownloadError("La galería supera el tamaño máximo permitido.")
    return total_bytes


def _ensure_temporary_space(directory: str, required_bytes: int) -> None:
    available = shutil.disk_usage(directory).free
    margin = max(TEMP_SPACE_MARGIN_BYTES, required_bytes // 20)
    if available < required_bytes + margin:
        raise PermanentDownloadError("No existe espacio temporal suficiente para preparar el ZIP.")


def _update_progress(download_id: int, processed: int) -> None:
    GalleryDownload.objects.filter(
        pk=download_id,
        status=GalleryDownload.Status.PROCESSING,
    ).update(processed_photos=processed, updated_at=timezone.now())


def _write_zip(download: GalleryDownload, photos, archive_path: str) -> None:
    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_STORED,
        allowZip64=True,
    ) as archive:
        for position, photo in enumerate(photos, start=1):
            member_name = zip_member_name(photo, position, len(photos))
            try:
                with photo.original_file.storage.open(photo.original_file.name, "rb") as source:
                    with archive.open(member_name, "w", force_zip64=True) as destination:
                        shutil.copyfileobj(source, destination, length=ZIP_COPY_CHUNK_SIZE)
            except Exception as exc:
                raise TransientDownloadError(
                    f"No fue posible leer el original {photo.uuid}."
                ) from exc
            if position % ZIP_PROGRESS_BATCH_SIZE == 0 or position == len(photos):
                _update_progress(download.pk, position)


def prepare_gallery_download(download_id: int) -> GalleryDownload | None:
    started = time.monotonic()
    with transaction.atomic():
        download = (
            GalleryDownload.objects.select_for_update()
            .select_related("gallery")
            .get(pk=download_id)
        )
        if download.status not in {
            GalleryDownload.Status.PENDING,
            GalleryDownload.Status.PROCESSING,
        }:
            return None
        download.status = GalleryDownload.Status.PROCESSING
        download.started_at = download.started_at or timezone.now()
        download.processed_photos = 0
        download.error = ""
        download.save(
            update_fields=["status", "started_at", "processed_photos", "error", "updated_at"]
        )

    gallery = download.gallery
    if gallery_content_fingerprint(gallery) != download.content_fingerprint:
        raise PermanentDownloadError("El contenido de la galería cambió antes de generar el ZIP.")
    photos = list(downloadable_photos(gallery))
    if len(photos) != download.photo_count:
        raise PermanentDownloadError("Cambió el número de fotografías de la galería.")
    expected_bytes = _validate_sources(photos)

    saved_name = ""
    with tempfile.TemporaryDirectory(prefix="mapache-gallery-download-") as temporary_dir:
        _ensure_temporary_space(temporary_dir, expected_bytes)
        archive_path = f"{temporary_dir}/gallery.zip"
        _write_zip(download, photos, archive_path)
        target_name = f"downloads/galleries/{gallery.uuid}/{download.uuid}.zip"
        try:
            if default_storage.exists(target_name):
                default_storage.delete(target_name)
            with open(archive_path, "rb") as archive_file:
                saved_name = default_storage.save(target_name, File(archive_file))
            if saved_name != target_name:
                raise OSError("El storage no conservó la key determinista del ZIP.")
            file_size = default_storage.size(saved_name)
        except Exception as exc:
            if saved_name:
                try:
                    default_storage.delete(saved_name)
                except Exception:
                    logger.exception(
                        "Partial ZIP cleanup failed gallery=%s download=%s",
                        gallery.uuid,
                        download.uuid,
                    )
            raise TransientDownloadError("No fue posible guardar el ZIP preparado.") from exc

    with transaction.atomic():
        locked = (
            GalleryDownload.objects.select_for_update()
            .select_related("gallery")
            .get(pk=download.pk)
        )
        if locked.status != GalleryDownload.Status.PROCESSING:
            default_storage.delete(saved_name)
            return locked
        if gallery_content_fingerprint(locked.gallery) != locked.content_fingerprint:
            default_storage.delete(saved_name)
            raise PermanentDownloadError("El contenido cambió durante la generación del ZIP.")
        completed_at = timezone.now()
        locked.file.name = saved_name
        locked.file_size = file_size
        locked.processed_photos = locked.photo_count
        locked.status = GalleryDownload.Status.READY
        locked.completed_at = completed_at
        locked.expires_at = completed_at + timedelta(seconds=settings.MAPACHE_GALLERY_DOWNLOAD_TTL)
        locked.error = ""
        locked.save(
            update_fields=[
                "file",
                "file_size",
                "processed_photos",
                "status",
                "completed_at",
                "expires_at",
                "error",
                "updated_at",
            ]
        )
    logger.info(
        "Gallery download ready gallery=%s download=%s photos=%s bytes=%s duration_ms=%s",
        gallery.uuid,
        download.uuid,
        len(photos),
        file_size,
        round((time.monotonic() - started) * 1000),
    )
    return locked


def mark_download_error(download_id: int, error: Exception | str) -> GalleryDownload | None:
    message = CONTROL_CHARACTERS.sub("", str(error)).strip()[:500]
    with transaction.atomic():
        download = (
            GalleryDownload.objects.select_for_update()
            .select_related("gallery")
            .filter(pk=download_id)
            .first()
        )
        if not download or download.status == GalleryDownload.Status.EXPIRED:
            return download
        if download.file and download.file.name:
            try:
                download.file.storage.delete(download.file.name)
            except Exception:
                logger.exception(
                    "Failed download cleanup gallery=%s download=%s",
                    download.gallery.uuid,
                    download.uuid,
                )
        download.status = GalleryDownload.Status.ERROR
        download.file = ""
        download.error = message or "No fue posible preparar la descarga."
        download.save(update_fields=["status", "file", "error", "updated_at"])
        logger.error(
            "Gallery download error gallery=%s download=%s error=%s",
            download.gallery.uuid,
            download.uuid,
            download.error,
        )
        return download


def cleanup_expired_downloads(*, now=None) -> int:
    now = now or timezone.now()
    candidates = GalleryDownload.objects.filter(
        status__in=[GalleryDownload.Status.READY, GalleryDownload.Status.ERROR],
    ).filter(expires_at__lte=now)
    count = 0
    for download in candidates.select_related("gallery").iterator():
        expire_download(download)
        count += 1
    return count


def invalidate_gallery_downloads(*, gallery: Gallery, invalidated_by) -> int:
    downloads = list(
        GalleryDownload.objects.filter(
            gallery=gallery,
            status__in=[
                GalleryDownload.Status.PENDING,
                GalleryDownload.Status.PROCESSING,
                GalleryDownload.Status.READY,
            ],
        ).select_related("gallery")
    )
    for download in downloads:
        expire_download(download)
    AuditLog.objects.create(
        user=invalidated_by,
        action="GALLERY_DOWNLOADS_INVALIDATED",
        model_name="Gallery",
        object_id=str(gallery.uuid),
        metadata={"count": len(downloads)},
    )
    return len(downloads)
