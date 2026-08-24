from django.core.management.base import BaseCommand, CommandError

from apps.galleries.models import Gallery
from apps.mapache_ai.models import GalleryAISettings


class Command(BaseCommand):
    help = "Programa la reindexación OCR de dorsales para una galería."

    def add_arguments(self, parser):
        parser.add_argument("gallery_uuid")

    def handle(self, *args, **options):
        try:
            gallery = Gallery.objects.get(uuid=options["gallery_uuid"])
            GalleryAISettings.objects.get(gallery=gallery, enabled=True, bib_search_enabled=True)
        except (Gallery.DoesNotExist, GalleryAISettings.DoesNotExist) as exc:
            raise CommandError(
                "La galería no existe o no tiene búsqueda por número activa."
            ) from exc
        from apps.mapache_ai.bib.tasks import index_gallery_bibs

        index_gallery_bibs.delay(gallery.pk)
        self.stdout.write(self.style.SUCCESS(f"Reindexación programada para {gallery.uuid}."))
