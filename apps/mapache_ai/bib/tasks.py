import logging

from celery import shared_task

from .services import (
    index_photo_bibs_now,
    prepare_gallery_bib_index,
    refresh_gallery_bib_progress,
)

logger = logging.getLogger("mapache.ai")


@shared_task(queue="ai")
def index_gallery_bibs(gallery_id: int):
    photo_ids = prepare_gallery_bib_index(gallery_id)
    for photo_id in photo_ids:
        index_photo_bibs.delay(photo_id)
    if not photo_ids:
        refresh_gallery_bib_progress(gallery_id)
    return {"gallery_id": gallery_id, "scheduled": len(photo_ids)}


@shared_task(bind=True, queue="ai", max_retries=2, default_retry_delay=60)
def index_photo_bibs(self, photo_id: int):
    try:
        count = index_photo_bibs_now(photo_id)
    except Exception as exc:
        logger.info("Retrying bib index for photo_id=%s", photo_id)
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1)) from exc
    return {"photo_id": photo_id, "bibs": count}
