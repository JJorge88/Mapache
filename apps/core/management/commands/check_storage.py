import logging
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger("mapache.storage")


class Command(BaseCommand):
    help = "Verifica escritura, lectura y eliminación del almacenamiento configurado."

    def handle(self, *args, **options):
        name = f"_mapache_storage_check/{uuid.uuid4}.txt"
        payload = b"mapache-storage-check"
        saved_name = ""
        self.stdout.write(f"Almacenamiento: {settings.STORAGE_BACKEND.upper()}")
        try:
            saved_name = default_storage.save(name, ContentFile(payload))
            if not default_storage.exists(saved_name):
                raise CommandError("El objeto temporal no existe después de escribirlo.")
            self.stdout.write("Escritura: CORRECTA")
            with default_storage.open(saved_name, "rb") as stored:
                if stored.read() != payload:
                    raise CommandError("El contenido leído no coincide con el escrito.")
            self.stdout.write("Lectura: CORRECTA")
            default_storage.delete(saved_name)
            if default_storage.exists(saved_name):
                raise CommandError("El objeto temporal continúa existiendo después de eliminarlo.")
            self.stdout.write("Eliminación: CORRECTA")
        except CommandError:
            logger.exception("Storage deep check failed backend=%s", settings.STORAGE_BACKEND)
            raise
        except Exception as exc:
            logger.exception("Storage deep check failed backend=%s", settings.STORAGE_BACKEND)
            raise CommandError("La verificación profunda del almacenamiento falló.") from exc
        finally:
            if saved_name:
                try:
                    default_storage.delete(saved_name)
                except Exception:
                    logger.exception("Could not clean storage check object")
