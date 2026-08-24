import logging

from celery import shared_task

from .downloads import (
    PermanentDownloadError,
    TransientDownloadError,
    cleanup_expired_downloads,
    mark_download_error,
    prepare_gallery_download,
)

logger = logging.getLogger("mapache.downloads")


@shared_task(bind=True, max_retries=3, default_retry_delay=30, queue="downloads")
def build_gallery_download(self, download_id: int):
    try:
        download = prepare_gallery_download(download_id)
    except PermanentDownloadError as exc:
        mark_download_error(download_id, exc)
        return {"download_id": download_id, "status": "error"}
    except TransientDownloadError as exc:
        if self.request.retries >= self.max_retries:
            mark_download_error(download_id, exc)
            return {"download_id": download_id, "status": "error"}
        countdown = min(30 * (2**self.request.retries), 300)
        logger.info("Retrying gallery download id=%s in %s seconds", download_id, countdown)
        raise self.retry(exc=exc, countdown=countdown) from exc
    if download is None:
        return {"download_id": download_id, "status": "ignored"}
    return {"download_id": download_id, "status": download.status}


@shared_task(queue="downloads")
def cleanup_expired_gallery_downloads():
    return {"expired": cleanup_expired_downloads()}


@shared_task(queue="media")
def cleanup_expired_direct_uploads():
    from .direct_uploads.services import cleanup_expired_uploads, direct_upload_available

    if not direct_upload_available():
        return {"expired": 0, "enabled": False}
    return {"expired": cleanup_expired_uploads(), "enabled": True}
