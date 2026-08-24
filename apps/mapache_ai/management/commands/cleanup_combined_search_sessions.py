from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.mapache_ai.combined.services import combined_results_cache_key
from apps.mapache_ai.models import CombinedSearchSession


class Command(BaseCommand):
    help = "Elimina sesiones expiradas de búsqueda combinada."

    def handle(self, *args, **options):
        sessions = list(
            CombinedSearchSession.objects.filter(expires_at__lte=timezone.now()).values_list(
                "uuid", flat=True
            )
        )
        for session_uuid in sessions:
            cache.delete(combined_results_cache_key(session_uuid))
        deleted, _details = CombinedSearchSession.objects.filter(uuid__in=sessions).delete()
        self.stdout.write(self.style.SUCCESS(f"Sesiones combinadas eliminadas: {deleted}"))
