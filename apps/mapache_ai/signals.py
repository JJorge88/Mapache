from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.galleries.models import Photo

from .models import BibPhotoAnalysis, DetectedBib, FaceEmbedding, GalleryAISettings, PhotoFaceIndex


@receiver(post_save, sender=Photo, dispatch_uid="mapache_ai_photo_lifecycle")
def handle_photo_ai_lifecycle(sender, instance: Photo, **kwargs) -> None:
    if instance.processing_status != Photo.ProcessingStatus.READY:
        FaceEmbedding.objects.filter(photo=instance).delete()
        PhotoFaceIndex.objects.filter(photo=instance).delete()
        DetectedBib.objects.filter(photo=instance).delete()
        BibPhotoAnalysis.objects.filter(photo=instance).delete()
        return
    if not instance.optimized_file:
        return
    try:
        ai_settings = GalleryAISettings.objects.get(gallery_id=instance.gallery_id, enabled=True)
    except GalleryAISettings.DoesNotExist:
        return
    if ai_settings.face_search_enabled:
        state, _created = PhotoFaceIndex.objects.get_or_create(
            gallery_id=instance.gallery_id, photo=instance
        )
        fresh = bool(
            state.status == PhotoFaceIndex.Status.READY
            and state.indexed_at
            and instance.processed_at
            and state.indexed_at >= instance.processed_at
        )
        if not fresh:
            state.status = PhotoFaceIndex.Status.PENDING
            state.error = ""
            state.save(update_fields=["status", "error", "updated_at"])
            from .tasks import index_photo_faces

            transaction.on_commit(lambda: index_photo_faces.delay(instance.pk))
    if ai_settings.bib_search_enabled:
        analysis, _created = BibPhotoAnalysis.objects.get_or_create(
            gallery_id=instance.gallery_id, photo=instance
        )
        fresh = bool(
            analysis.status == BibPhotoAnalysis.Status.READY
            and analysis.processed_at
            and instance.processed_at
            and analysis.processed_at >= instance.processed_at
        )
        if not fresh:
            analysis.status = BibPhotoAnalysis.Status.PENDING
            analysis.error = ""
            analysis.save(update_fields=["status", "error", "updated_at"])
            from .bib.tasks import index_photo_bibs

            transaction.on_commit(lambda: index_photo_bibs.delay(instance.pk))
