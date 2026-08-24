from django.core.management.base import BaseCommand

from apps.galleries.downloads import cleanup_expired_downloads


class Command(BaseCommand):
    help = "Elimina ZIPs vencidos y marca sus descargas como expiradas."

    def handle(self, *args, **options):
        count = cleanup_expired_downloads()
        self.stdout.write(self.style.SUCCESS(f"Expired downloads cleaned: {count}"))
