from django.core.management.base import BaseCommand

from apps.galleries.direct_uploads.services import cleanup_expired_uploads, direct_upload_available


class Command(BaseCommand):
    help = "Aborta y limpia cargas directas vencidas."

    def handle(self, *args, **options):
        if not direct_upload_available():
            self.stdout.write("Carga directa desactivada; no hay nada que limpiar.")
            return
        count = cleanup_expired_uploads()
        self.stdout.write(self.style.SUCCESS(f"Cargas vencidas limpiadas: {count}"))
