import logging
import math
import re
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.galleries.models import GalleryUploadBatch, GalleryUploadItem, Photo
from apps.galleries.services import add_photo
from apps.media_processing.services import FORMAT_DETAILS

from .client import get_direct_upload_client

logger = logging.getLogger("mapache.uploads")
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
ACTIVE_BATCH_STATUSES = {
    GalleryUploadBatch.Status.CREATED,
    GalleryUploadBatch.Status.UPLOADING,
    GalleryUploadBatch.Status.PROCESSING,
    GalleryUploadBatch.Status.PARTIAL,
}
TERMINAL_ITEM_STATUSES = {
    GalleryUploadItem.Status.READY,
    GalleryUploadItem.Status.ERROR,
    GalleryUploadItem.Status.ABORTED,
    GalleryUploadItem.Status.EXPIRED,
}


def direct_upload_available() -> bool:
    return bool(settings.MAPACHE_DIRECT_UPLOAD_ENABLED and settings.STORAGE_BACKEND == "r2")


def _safe_filename(name: str) -> str:
    normalized = CONTROL_CHARACTERS.sub("", str(name or "")).replace("\\", "/")
    return normalized.rsplit("/", 1)[-1].strip()[:255]


def _validate_metadata(metadata):
    if not isinstance(metadata, list) or not metadata:
        raise ValidationError("Envía al menos un archivo.")
    if len(metadata) > settings.MAPACHE_DIRECT_UPLOAD_MAX_FILES:
        raise ValidationError("La selección supera el máximo de archivos permitido.")
    allowed = {
        extension: details["mime"]
        for details in FORMAT_DETAILS.values()
        for extension in details["extensions"]
    }
    validated = []
    for raw in metadata:
        if not isinstance(raw, dict):
            raise ValidationError("La información del archivo no es válida.")
        name = _safe_filename(raw.get("name"))
        extension = Path(name).suffix.lower()
        if extension not in allowed:
            raise ValidationError(f"Formato no permitido: {name or 'archivo'}.")
        try:
            size = int(raw.get("size"))
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"Tamaño inválido: {name}.") from exc
        max_size = settings.MAPACHE_MAX_PHOTO_SIZE_MB * 1024 * 1024
        if size <= 0 or size > max_size:
            raise ValidationError(f"Tamaño no permitido: {name}.")
        content_type = str(raw.get("type") or allowed[extension]).lower()
        if content_type != allowed[extension]:
            raise ValidationError(f"Tipo de contenido no permitido: {name}.")
        last_modified = raw.get("last_modified")
        try:
            last_modified = int(last_modified) if last_modified is not None else None
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"Fecha de modificación inválida: {name}.") from exc
        validated.append(
            {
                "name": name,
                "size": size,
                "content_type": content_type,
                "last_modified": last_modified,
                "extension": ".jpg" if extension == ".jpeg" else extension,
            }
        )
    return validated


def _part_size_bytes() -> int:
    return settings.MAPACHE_MULTIPART_PART_SIZE_MB * 1024 * 1024


def total_parts(item: GalleryUploadItem) -> int:
    return math.ceil(item.expected_size / _part_size_bytes())


def _item_payload(item, client):
    payload = {
        "upload_item_uuid": str(item.uuid),
        "name": item.original_filename,
        "size": item.expected_size,
        "last_modified": item.last_modified,
        "mode": item.upload_mode,
        "expires_at": item.expires_at.isoformat(),
    }
    if item.upload_mode == GalleryUploadItem.UploadMode.SINGLE:
        payload["upload_url"] = client.presign_put(
            key=item.object_key,
            content_type=item.content_type,
            expires=settings.MAPACHE_UPLOAD_URL_TTL,
        )
    else:
        payload["part_size"] = _part_size_bytes()
        payload["total_parts"] = total_parts(item)
    return payload


def _presign_rate_limit(user_id: int, count: int) -> None:
    key = f"direct-upload-presign:{user_id}:{timezone.now():%Y%m%d%H%M}"
    try:
        current = cache.incr(key, count)
    except ValueError:
        cache.set(key, count, 70)
        current = count
    if current > max(2000, settings.MAPACHE_DIRECT_UPLOAD_MAX_FILES * 4):
        raise ValidationError("Se solicitaron demasiados enlaces. Espera un minuto.")


@transaction.atomic
def initialize_uploads(*, gallery, user, metadata, batch_uuid=None, client=None):
    if not direct_upload_available():
        raise ValidationError("La carga directa no está disponible.")
    files = _validate_metadata(metadata)
    _presign_rate_limit(user.pk, len(files))
    client = client or get_direct_upload_client()
    now = timezone.now()
    expires_at = now + timedelta(seconds=settings.MAPACHE_UPLOAD_SESSION_TTL)
    if batch_uuid:
        try:
            batch = GalleryUploadBatch.objects.select_for_update().get(
                uuid=batch_uuid,
                gallery=gallery,
                created_by=user,
                status__in=ACTIVE_BATCH_STATUSES,
                expires_at__gt=now,
            )
        except GalleryUploadBatch.DoesNotExist as exc:
            raise ValidationError("El lote no pertenece a esta galería o ya expiró.") from exc
    else:
        batch = GalleryUploadBatch.objects.create(
            gallery=gallery,
            created_by=user,
            expires_at=expires_at,
        )
        AuditLog.objects.create(
            user=user,
            action="UPLOAD_BATCH_CREATED",
            model_name="Gallery",
            object_id=str(gallery.uuid),
            metadata={"batch_uuid": str(batch.uuid)},
        )
    existing_count = batch.items.count()
    existing_bytes = batch.items.aggregate(total=Sum("expected_size"))["total"] or 0
    created_multipart = []
    items = []
    assigned_item_ids = set()
    new_count = 0
    new_bytes = 0
    try:
        for metadata_item in files:
            reusable = (
                batch.items.exclude(pk__in=assigned_item_ids)
                .filter(
                    original_filename=metadata_item["name"],
                    expected_size=metadata_item["size"],
                    last_modified=metadata_item["last_modified"],
                    photo__isnull=True,
                    status__in=[
                        GalleryUploadItem.Status.PENDING,
                        GalleryUploadItem.Status.UPLOADING,
                        GalleryUploadItem.Status.UPLOADED,
                    ],
                )
                .first()
            )
            if reusable:
                items.append(reusable)
                assigned_item_ids.add(reusable.pk)
                continue
            new_count += 1
            new_bytes += metadata_item["size"]
            if existing_count + new_count > settings.MAPACHE_DIRECT_UPLOAD_MAX_FILES:
                raise ValidationError("El lote supera el máximo de archivos permitido.")
            if existing_bytes + new_bytes > settings.MAPACHE_DIRECT_UPLOAD_MAX_TOTAL_BYTES:
                raise ValidationError("El lote supera el tamaño total permitido.")
            reserved_uuid = uuid.uuid4()
            object_key = (
                f"galleries/{gallery.uuid}/originals/{reserved_uuid}{metadata_item['extension']}"
            )
            mode = (
                GalleryUploadItem.UploadMode.MULTIPART
                if metadata_item["size"]
                > settings.MAPACHE_MULTIPART_UPLOAD_THRESHOLD_MB * 1024 * 1024
                else GalleryUploadItem.UploadMode.SINGLE
            )
            upload_id = ""
            if mode == GalleryUploadItem.UploadMode.MULTIPART:
                if math.ceil(metadata_item["size"] / _part_size_bytes()) > 10000:
                    raise ValidationError("El archivo requiere demasiadas partes.")
                upload_id = client.create_multipart(
                    key=object_key,
                    content_type=metadata_item["content_type"],
                )
                created_multipart.append((object_key, upload_id))
            item = GalleryUploadItem.objects.create(
                batch=batch,
                gallery=gallery,
                reserved_photo_uuid=reserved_uuid,
                original_filename=metadata_item["name"],
                object_key=object_key,
                expected_size=metadata_item["size"],
                content_type=metadata_item["content_type"],
                last_modified=metadata_item["last_modified"],
                upload_mode=mode,
                multipart_upload_id=upload_id,
                expires_at=expires_at,
            )
            items.append(item)
            assigned_item_ids.add(item.pk)
    except Exception:
        for key, upload_id in created_multipart:
            try:
                client.abort_multipart(key=key, upload_id=upload_id)
            except Exception:
                logger.exception("No se pudo revertir la carga por partes batch=%s", batch.uuid)
        raise
    batch.status = GalleryUploadBatch.Status.UPLOADING
    batch.expires_at = expires_at
    batch.total_files = batch.items.count()
    batch.total_bytes = batch.items.aggregate(total=Sum("expected_size"))["total"] or 0
    batch.save(update_fields=["status", "expires_at", "total_files", "total_bytes", "updated_at"])
    logger.info(
        "Upload batch initialized batch=%s gallery=%s items=%s total_bytes=%s",
        batch.uuid,
        gallery.uuid,
        len(items),
        batch.total_bytes,
    )
    return batch, [_item_payload(item, client) for item in items]


def list_uploaded_parts(item, client=None):
    if item.upload_mode != GalleryUploadItem.UploadMode.MULTIPART:
        return []
    client = client or get_direct_upload_client()
    return client.list_parts(key=item.object_key, upload_id=item.multipart_upload_id)


def generate_part_urls(*, item, part_numbers, user, client=None):
    if item.expires_at <= timezone.now() or item.status in TERMINAL_ITEM_STATUSES:
        raise ValidationError("La sesión de carga ya no está activa.")
    if not isinstance(part_numbers, list) or not part_numbers or len(part_numbers) > 100:
        raise ValidationError("Solicita entre 1 y 100 partes.")
    requested = []
    maximum = total_parts(item)
    for raw in part_numbers:
        try:
            number = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Número de parte inválido.") from exc
        if number < 1 or number > maximum or number in requested:
            raise ValidationError("Número de parte inválido.")
        requested.append(number)
    _presign_rate_limit(user.pk, len(requested))
    client = client or get_direct_upload_client()
    existing = list_uploaded_parts(item, client)
    existing_numbers = {part["part_number"] for part in existing}
    urls = [
        {
            "part_number": number,
            "upload_url": client.presign_part(
                key=item.object_key,
                upload_id=item.multipart_upload_id,
                part_number=number,
                expires=settings.MAPACHE_UPLOAD_URL_TTL,
            ),
        }
        for number in requested
        if number not in existing_numbers
    ]
    if item.status == GalleryUploadItem.Status.PENDING:
        item.status = GalleryUploadItem.Status.UPLOADING
        item.save(update_fields=["status", "updated_at"])
    return urls, existing


def _verify_object(item, client):
    try:
        head = client.head(key=item.object_key)
    except Exception as exc:
        raise ValidationError("El objeto subido no está disponible.") from exc
    if int(head.get("ContentLength", -1)) != item.expected_size:
        raise ValidationError("El tamaño del objeto no coincide con el esperado.")
    return head


def _refresh_batch(batch):
    items = batch.items.select_related("photo")
    batch.completed_files = items.filter(photo__isnull=False).count()
    batch.failed_files = items.filter(
        status__in=[GalleryUploadItem.Status.ERROR, GalleryUploadItem.Status.ABORTED]
    ).count()
    batch.uploaded_bytes = (
        items.filter(photo__isnull=False).aggregate(total=Sum("expected_size"))["total"] or 0
    )
    total = batch.total_files
    ready = items.filter(status=GalleryUploadItem.Status.READY).count()
    processing = items.filter(
        status__in=[
            GalleryUploadItem.Status.CONFIRMED,
            GalleryUploadItem.Status.PROCESSING,
        ]
    ).exists()
    if total and ready == total:
        batch.status = GalleryUploadBatch.Status.COMPLETED
    elif batch.completed_files and processing:
        batch.status = GalleryUploadBatch.Status.PROCESSING
    elif batch.failed_files and batch.completed_files + batch.failed_files == total:
        batch.status = GalleryUploadBatch.Status.PARTIAL
    elif batch.failed_files:
        batch.status = GalleryUploadBatch.Status.PARTIAL
    else:
        batch.status = GalleryUploadBatch.Status.UPLOADING
    batch.save(
        update_fields=[
            "completed_files",
            "failed_files",
            "uploaded_bytes",
            "status",
            "updated_at",
        ]
    )


@transaction.atomic
def confirm_upload(*, item, user, parts=None, client=None):
    item = (
        GalleryUploadItem.objects.select_for_update()
        .select_related("batch", "gallery")
        .get(pk=item.pk)
    )
    if item.photo_id:
        return item
    if item.expires_at <= timezone.now() or item.status in {
        GalleryUploadItem.Status.ABORTED,
        GalleryUploadItem.Status.EXPIRED,
    }:
        raise ValidationError("La sesión de carga expiró o fue cancelada.")
    client = client or get_direct_upload_client()
    if item.upload_mode == GalleryUploadItem.UploadMode.MULTIPART:
        try:
            head = client.head(key=item.object_key)
        except Exception:
            head = None
        if head is None:
            expected = list(range(1, total_parts(item) + 1))
            if not isinstance(parts, list):
                raise ValidationError("Faltan partes de la carga.")
            normalized = []
            for part in parts:
                if not isinstance(part, dict):
                    raise ValidationError("La lista de partes no es válida.")
                try:
                    number = int(part["part_number"])
                    etag = str(part["etag"]).strip()
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValidationError("La lista de partes no es válida.") from exc
                if not etag:
                    raise ValidationError("Falta el identificador ETag de una parte.")
                normalized.append({"part_number": number, "etag": etag})
            normalized.sort(key=lambda value: value["part_number"])
            if [part["part_number"] for part in normalized] != expected:
                raise ValidationError("La lista de partes está incompleta.")
            client.complete_multipart(
                key=item.object_key,
                upload_id=item.multipart_upload_id,
                parts=normalized,
            )
            _verify_object(item, client)
        elif int(head.get("ContentLength", -1)) != item.expected_size:
            raise ValidationError("El tamaño del objeto no coincide con el esperado.")
    else:
        _verify_object(item, client)

    photo = add_photo(
        gallery=item.gallery,
        original_file=item.object_key,
        uploaded_by=user,
        filename=Path(item.object_key).name,
        original_filename=item.original_filename,
        mime_type=item.content_type,
        file_size=item.expected_size,
        photo_uuid=item.reserved_photo_uuid,
    )
    item.photo = photo
    item.status = GalleryUploadItem.Status.CONFIRMED
    item.completed_at = timezone.now()
    item.error = ""
    item.save(update_fields=["photo", "status", "completed_at", "error", "updated_at"])
    logger.info(
        "Upload confirmed batch=%s item=%s gallery=%s size=%s mode=%s status=%s",
        item.batch.uuid,
        item.uuid,
        item.gallery.uuid,
        item.expected_size,
        item.upload_mode,
        item.status,
    )
    _refresh_batch(item.batch)
    from apps.media_processing.tasks import process_photo

    transaction.on_commit(lambda: process_photo.delay(photo.pk))
    return item


def sync_item_processing_states(batch):
    for item in batch.items.select_related("photo"):
        if not item.photo_id:
            continue
        desired = {
            Photo.ProcessingStatus.PENDING: GalleryUploadItem.Status.CONFIRMED,
            Photo.ProcessingStatus.PROCESSING: GalleryUploadItem.Status.PROCESSING,
            Photo.ProcessingStatus.READY: GalleryUploadItem.Status.READY,
            Photo.ProcessingStatus.ERROR: GalleryUploadItem.Status.ERROR,
        }[item.photo.processing_status]
        if item.status != desired:
            item.status = desired
            item.error = (
                item.photo.processing_error if desired == GalleryUploadItem.Status.ERROR else ""
            )
            item.save(update_fields=["status", "error", "updated_at"])
    _refresh_batch(batch)


@transaction.atomic
def abort_upload(*, item, user, client=None):
    item = (
        GalleryUploadItem.objects.select_for_update()
        .select_related("batch", "gallery")
        .get(pk=item.pk)
    )
    if item.photo_id:
        raise ValidationError("Una fotografía confirmada ya no puede abortarse.")
    if item.status in {GalleryUploadItem.Status.ABORTED, GalleryUploadItem.Status.EXPIRED}:
        return item
    client = client or get_direct_upload_client()
    try:
        if item.upload_mode == GalleryUploadItem.UploadMode.MULTIPART:
            client.abort_multipart(key=item.object_key, upload_id=item.multipart_upload_id)
        else:
            client.delete(key=item.object_key)
    except Exception:
        logger.exception(
            "Upload abort provider failure item=%s gallery=%s",
            item.uuid,
            item.gallery_id,
        )
    item.status = GalleryUploadItem.Status.ABORTED
    item.error = ""
    item.save(update_fields=["status", "error", "updated_at"])
    logger.info(
        "Upload aborted batch=%s item=%s gallery=%s mode=%s",
        item.batch.uuid,
        item.uuid,
        item.gallery.uuid,
        item.upload_mode,
    )
    _refresh_batch(item.batch)
    if not item.batch.items.exclude(status=GalleryUploadItem.Status.ABORTED).exists():
        item.batch.status = GalleryUploadBatch.Status.ABORTED
        item.batch.save(update_fields=["status", "updated_at"])
        AuditLog.objects.create(
            user=user,
            action="UPLOAD_BATCH_ABORTED",
            model_name="Gallery",
            object_id=str(item.gallery.uuid),
            metadata={"batch_uuid": str(item.batch.uuid)},
        )
    return item


def cleanup_expired_uploads(*, client=None):
    client = client or get_direct_upload_client()
    now = timezone.now()
    count = 0
    items = GalleryUploadItem.objects.filter(
        photo__isnull=True,
        expires_at__lte=now,
    ).exclude(status__in=[GalleryUploadItem.Status.EXPIRED, GalleryUploadItem.Status.ABORTED])
    for item in items.select_related("batch").iterator():
        try:
            if item.upload_mode == GalleryUploadItem.UploadMode.MULTIPART:
                client.abort_multipart(key=item.object_key, upload_id=item.multipart_upload_id)
            else:
                client.delete(key=item.object_key)
        except Exception:
            logger.exception("Expired upload cleanup provider failure item=%s", item.uuid)
        item.status = GalleryUploadItem.Status.EXPIRED
        item.save(update_fields=["status", "updated_at"])
        count += 1
    GalleryUploadBatch.objects.filter(expires_at__lte=now).exclude(
        status__in=[GalleryUploadBatch.Status.COMPLETED, GalleryUploadBatch.Status.ABORTED]
    ).update(status=GalleryUploadBatch.Status.EXPIRED, updated_at=now)
    return count
