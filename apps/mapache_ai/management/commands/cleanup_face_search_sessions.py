from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.mapache_ai.models import FaceSearchSession
from apps.mapache_ai.services import results_cache_key


class Command(BaseCommand):
    help = "Elimina sesiones de búsqueda facial expiradas y sus resultados temporales."

    def handle(self, *args, **options):
        sessions = list(
            FaceSearchSession.objects.filter(expires_at__lte=timezone.now()).only("id", "uuid")
        )
        for session in sessions:
            cache.delete(results_cache_key(session.uuid))
        deleted, _details = FaceSearchSession.objects.filter(
            id__in=[session.id for session in sessions]
        ).delete()
        self.stdout.write(self.style.SUCCESS(f"Sesiones expiradas eliminadas: {deleted}"))
