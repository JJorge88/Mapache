from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.mapache_ai.bib.services import bib_results_cache_key
from apps.mapache_ai.models import BibSearchSession


class Command(BaseCommand):
    help = "Elimina sesiones expiradas de búsqueda por dorsal."

    def handle(self, *args, **options):
        sessions = list(
            BibSearchSession.objects.filter(expires_at__lte=timezone.now()).values_list(
                "uuid", flat=True
            )
        )
        for session_uuid in sessions:
            cache.delete(bib_results_cache_key(session_uuid))
        deleted, _details = BibSearchSession.objects.filter(uuid__in=sessions).delete()
        self.stdout.write(self.style.SUCCESS(f"Sesiones de dorsal eliminadas: {deleted}"))
