import logging

from celery import shared_task

from .exceptions import PermanentImageError, TransientProcessingError
from .services import process_photo_image

logger = logging.getLogger("mapache.media_processing")


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def process_photo(self, photo_id: int):
    try:
        photo = process_photo_image(photo_id)
    except PermanentImageError:
        return {"photo_id": photo_id, "status": "error"}
    except TransientProcessingError as exc:
        countdown = min(30 * (2**self.request.retries), 300)
        logger.info("Retrying photo %s in %s seconds", photo_id, countdown)
        raise self.retry(exc=exc, countdown=countdown) from exc
    if photo is None:
        return {"photo_id": photo_id, "status": "missing"}
    return {"photo_id": photo_id, "status": photo.processing_status}
