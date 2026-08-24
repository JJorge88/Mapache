import logging

from celery import shared_task

from .bib.tasks import index_gallery_bibs, index_photo_bibs  # noqa: F401
from .services import index_photo_faces_now, prepare_gallery_index, refresh_gallery_index_progress

logger = logging.getLogger("mapache.ai")


@shared_task(queue="ai")
def index_gallery_faces(gallery_id: int):
    photo_ids = prepare_gallery_index(gallery_id)
    for photo_id in photo_ids:
        index_photo_faces.delay(photo_id)
    if not photo_ids:
        refresh_gallery_index_progress(gallery_id)
    return {"gallery_id": gallery_id, "scheduled": len(photo_ids)}


@shared_task(bind=True, queue="ai", max_retries=2, default_retry_delay=60)
def index_photo_faces(self, photo_id: int):
    try:
        count = index_photo_faces_now(photo_id)
    except Exception as exc:
        logger.info("Retrying AI index for photo_id=%s", photo_id)
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1)) from exc
    return {"photo_id": photo_id, "faces": count}
