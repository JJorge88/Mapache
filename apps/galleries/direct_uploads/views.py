import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from apps.galleries.models import Gallery, GalleryUploadBatch, GalleryUploadItem

from .client import get_direct_upload_client
from .services import (
    abort_upload,
    confirm_upload,
    direct_upload_available,
    generate_part_urls,
    initialize_uploads,
    list_uploaded_parts,
    sync_item_processing_states,
)

logger = logging.getLogger("mapache.uploads")


def _json(request):
    try:
        value = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError("El contenido enviado no es JSON válido.") from exc
    if not isinstance(value, dict):
        raise ValidationError("El contenido enviado no es válido.")
    return value


def _error(exc, status=400):
    if isinstance(exc, ValidationError):
        message = exc.messages[0]
    else:
        logger.exception("Direct upload provider error")
        message = "El almacenamiento no respondió. Intenta nuevamente."
    return JsonResponse({"error": message}, status=status)


def _available():
    if not direct_upload_available():
        raise Http404


def _owned_item(user, item_uuid):
    return get_object_or_404(
        GalleryUploadItem.objects.select_related("batch", "gallery", "photo"),
        uuid=item_uuid,
        batch__created_by=user,
    )


@require_POST
@login_required
def upload_init(request, gallery_uuid):
    _available()
    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    try:
        data = _json(request)
        batch, items = initialize_uploads(
            gallery=gallery,
            user=request.user,
            metadata=data.get("files"),
            batch_uuid=data.get("batch_uuid") or None,
        )
    except ValidationError as exc:
        return _error(exc)
    except Exception as exc:
        return _error(exc, 503)
    return JsonResponse(
        {
            "batch_uuid": str(batch.uuid),
            "expires_at": batch.expires_at.isoformat(),
            "concurrency": settings.MAPACHE_UPLOAD_CONCURRENCY,
            "items": items,
        },
        status=201,
    )


@require_POST
@login_required
def upload_parts(request, item_uuid):
    _available()
    item = _owned_item(request.user, item_uuid)
    if item.upload_mode != GalleryUploadItem.UploadMode.MULTIPART:
        return JsonResponse({"error": "Este archivo no utiliza carga por partes."}, status=409)
    try:
        urls, existing = generate_part_urls(
            item=item,
            part_numbers=_json(request).get("part_numbers"),
            user=request.user,
        )
    except ValidationError as exc:
        return _error(exc)
    except Exception as exc:
        return _error(exc, 503)
    return JsonResponse({"parts": urls, "uploaded_parts": existing})


@require_POST
@login_required
def upload_complete(request, item_uuid):
    _available()
    item = _owned_item(request.user, item_uuid)
    try:
        item = confirm_upload(
            item=item,
            user=request.user,
            parts=_json(request).get("parts"),
        )
    except ValidationError as exc:
        return _error(exc, 409)
    except Exception as exc:
        return _error(exc, 503)
    return JsonResponse(
        {
            "upload_item_uuid": str(item.uuid),
            "status": item.status,
            "photo_uuid": str(item.photo.uuid),
            "photo_status": item.photo.processing_status,
        }
    )


@require_POST
@login_required
def upload_abort(request, item_uuid):
    _available()
    item = _owned_item(request.user, item_uuid)
    try:
        item = abort_upload(item=item, user=request.user)
    except ValidationError as exc:
        return _error(exc, 409)
    except Exception as exc:
        return _error(exc, 503)
    return JsonResponse({"upload_item_uuid": str(item.uuid), "status": item.status})


@require_GET
@login_required
def upload_resume(request, batch_uuid):
    _available()
    batch = get_object_or_404(
        GalleryUploadBatch.objects.select_related("gallery"),
        uuid=batch_uuid,
        created_by=request.user,
    )
    try:
        sync_item_processing_states(batch)
        client = get_direct_upload_client()
        items = []
        for item in batch.items.select_related("photo"):
            parts = []
            if (
                item.upload_mode == GalleryUploadItem.UploadMode.MULTIPART
                and not item.photo_id
                and item.status
                not in {GalleryUploadItem.Status.ABORTED, GalleryUploadItem.Status.EXPIRED}
            ):
                parts = list_uploaded_parts(item, client)
            items.append(
                {
                    "upload_item_uuid": str(item.uuid),
                    "name": item.original_filename,
                    "size": item.expected_size,
                    "last_modified": item.last_modified,
                    "mode": item.upload_mode,
                    "status": item.status,
                    "error": item.error,
                    "photo_uuid": str(item.photo.uuid) if item.photo_id else None,
                    "photo_status": item.photo.processing_status if item.photo_id else None,
                    "uploaded_parts": parts,
                }
            )
    except Exception as exc:
        return _error(exc, 503)
    return JsonResponse(
        {
            "batch_uuid": str(batch.uuid),
            "gallery_uuid": str(batch.gallery.uuid),
            "status": batch.status,
            "total_files": batch.total_files,
            "completed_files": batch.completed_files,
            "failed_files": batch.failed_files,
            "total_bytes": batch.total_bytes,
            "uploaded_bytes": batch.uploaded_bytes,
            "expires_at": batch.expires_at.isoformat(),
            "items": items,
        }
    )
