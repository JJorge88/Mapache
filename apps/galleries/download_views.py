import logging

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST

from .downloads import (
    PermanentDownloadError,
    expire_download,
    gallery_content_fingerprint,
    request_gallery_download,
    sanitize_download_filename,
    session_authorization_hash,
)
from .models import Gallery, GalleryDownload, Photo

logger = logging.getLogger("mapache.downloads")


def _public_gallery(request, slug: str, *, gallery_download: bool = False) -> Gallery:
    gallery = get_object_or_404(Gallery, slug=slug, status=Gallery.Status.PUBLISHED)
    flag = gallery.allow_gallery_download if gallery_download else gallery.allow_photo_download
    if not flag:
        raise Http404
    if gallery.visibility == Gallery.Visibility.PRIVATE_PIN and not request.session.get(
        f"gallery_access_{gallery.uuid}", False
    ):
        raise Http404
    return gallery


def _authorized_download(request, slug: str, download_uuid) -> GalleryDownload:
    gallery = _public_gallery(request, slug, gallery_download=True)
    authorization_hash = session_authorization_hash(request)
    if not authorization_hash:
        raise Http404
    return get_object_or_404(
        GalleryDownload.objects.select_related("gallery"),
        uuid=download_uuid,
        gallery=gallery,
        authorization_hash=authorization_hash,
    )


def photo_download(request, slug: str, photo_uuid) -> HttpResponse:
    gallery = _public_gallery(request, slug)
    photo = get_object_or_404(
        gallery.photos,
        uuid=photo_uuid,
        processing_status=Photo.ProcessingStatus.READY,
    )
    if not photo.original_file or not photo.original_file.name:
        raise Http404
    try:
        if not photo.original_file.storage.exists(photo.original_file.name):
            raise Http404
    except Http404:
        raise
    except Exception as exc:
        logger.exception(
            "Photo download availability failed gallery=%s photo=%s",
            gallery.uuid,
            photo.uuid,
        )
        raise Http404 from exc
    filename = sanitize_download_filename(
        photo.original_filename,
        fallback=f"foto-{photo.uuid}.jpg",
    )
    disposition = content_disposition_header(True, filename)
    if settings.STORAGE_BACKEND == "r2":
        try:
            url = photo.original_file.storage.url(
                photo.original_file.name,
                parameters={
                    "ResponseContentDisposition": disposition,
                    "ResponseContentType": photo.mime_type or "application/octet-stream",
                },
                expire=settings.MAPACHE_DOWNLOAD_URL_TTL,
            )
        except Exception as exc:
            logger.exception(
                "Photo download signing failed gallery=%s photo=%s", gallery.uuid, photo.uuid
            )
            raise Http404 from exc
        return HttpResponseRedirect(url)
    try:
        source = photo.original_file.storage.open(photo.original_file.name, "rb")
    except Exception as exc:
        logger.exception("Photo download read failed gallery=%s photo=%s", gallery.uuid, photo.uuid)
        raise Http404 from exc
    return FileResponse(
        source,
        as_attachment=True,
        filename=filename,
        content_type=photo.mime_type or "application/octet-stream",
    )


@require_POST
def gallery_download_request(request, slug: str) -> HttpResponse:
    gallery = _public_gallery(request, slug, gallery_download=True)
    try:
        download, _created = request_gallery_download(gallery=gallery, request=request)
    except PermanentDownloadError as exc:
        messages.error(request, str(exc))
        return redirect("galleries_public:detail", slug=gallery.slug)
    return redirect(
        "galleries_public:download_prepare",
        slug=gallery.slug,
        download_uuid=download.uuid,
    )


def gallery_download_prepare(request, slug: str, download_uuid) -> HttpResponse:
    download = _authorized_download(request, slug, download_uuid)
    if (
        download.status == GalleryDownload.Status.READY
        and download.expires_at
        and download.expires_at <= timezone.now()
    ):
        download = expire_download(download)
    return render(
        request,
        "galleries/download_prepare.html",
        {
            "gallery": download.gallery,
            "download": download,
            "status_url": reverse(
                "galleries_public:download_status",
                args=[download.gallery.slug, download.uuid],
            ),
            "download_url": reverse(
                "galleries_public:download_file",
                args=[download.gallery.slug, download.uuid],
            ),
        },
    )


def gallery_download_status(request, slug: str, download_uuid) -> JsonResponse:
    download = _authorized_download(request, slug, download_uuid)
    if (
        download.status == GalleryDownload.Status.READY
        and download.expires_at
        and download.expires_at <= timezone.now()
    ):
        download = expire_download(download)
    return JsonResponse(
        {
            "status": download.status,
            "processed": download.processed_photos,
            "total": download.photo_count,
            "ready": download.status == GalleryDownload.Status.READY,
            "file_size": (
                download.file_size if download.status == GalleryDownload.Status.READY else 0
            ),
            "expires_at": (
                download.expires_at.isoformat()
                if download.status == GalleryDownload.Status.READY and download.expires_at
                else None
            ),
        }
    )


def gallery_download_file(request, slug: str, download_uuid) -> HttpResponse:
    download = _authorized_download(request, slug, download_uuid)
    now = timezone.now()
    if download.status != GalleryDownload.Status.READY:
        raise Http404
    if not download.expires_at or download.expires_at <= now:
        expire_download(download)
        raise Http404
    if gallery_content_fingerprint(download.gallery) != download.content_fingerprint:
        raise Http404
    if not download.file or not download.file.name:
        raise Http404
    try:
        if not download.file.storage.exists(download.file.name):
            raise Http404
    except Http404:
        raise
    except Exception as exc:
        logger.exception(
            "Gallery download availability failed gallery=%s download=%s",
            download.gallery.uuid,
            download.uuid,
        )
        raise Http404 from exc
    filename = sanitize_download_filename(
        f"{download.gallery.slug}.zip",
        fallback=f"galeria-{download.gallery.uuid}.zip",
    )
    disposition = content_disposition_header(True, filename)
    if settings.STORAGE_BACKEND == "r2":
        try:
            url = download.file.storage.url(
                download.file.name,
                parameters={
                    "ResponseContentDisposition": disposition,
                    "ResponseContentType": "application/zip",
                },
                expire=settings.MAPACHE_DOWNLOAD_URL_TTL,
            )
        except Exception as exc:
            logger.exception(
                "Gallery download signing failed gallery=%s download=%s",
                download.gallery.uuid,
                download.uuid,
            )
            raise Http404 from exc
        return HttpResponseRedirect(url)
    try:
        source = download.file.storage.open(download.file.name, "rb")
    except Exception as exc:
        logger.exception(
            "Gallery download read failed gallery=%s download=%s",
            download.gallery.uuid,
            download.uuid,
        )
        raise Http404 from exc
    return FileResponse(
        source,
        as_attachment=True,
        filename=filename,
        content_type="application/zip",
    )
