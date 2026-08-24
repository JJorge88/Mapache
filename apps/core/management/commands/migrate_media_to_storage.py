import logging
from hashlib import sha256

from django.conf import settings
from django.core.files.storage import FileSystemStorage, default_storage
from django.core.management.base import BaseCommand, CommandError

from apps.galleries.models import Gallery, Photo

logger = logging.getLogger("mapache.storage")


def _digest(storage, name):
    checksum = sha256()
    with storage.open(name, "rb") as stored:
        for chunk in iter(lambda: stored.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.digest()


def photo_object_names(photos):
    for photo in photos.iterator():
        for field_file in (
            photo.original_file,
            photo.optimized_file,
            photo.thumbnail_file,
        ):
            if field_file and field_file.name:
                yield field_file.name


def copy_media_objects(*, source, destination, names, dry_run=False, stdout=None):
    counters = {"copied": 0, "skipped": 0, "failed": 0, "bytes": 0}
    for name in dict.fromkeys(names):
        try:
            if not source.exists(name):
                counters["failed"] += 1
                if stdout:
                    stdout.write(f"FALLIDO {name} (no existe en el origen)")
                continue
            source_size = source.size(name)
            if destination.exists(name):
                if destination.size(name) == source_size and _digest(source, name) == _digest(
                    destination, name
                ):
                    counters["skipped"] += 1
                    if stdout:
                        stdout.write(f"OMITIDO {name}")
                    continue
                raise OSError("El destino ya contiene una key diferente; no se sobrescribió.")
            if dry_run:
                if stdout:
                    stdout.write(f"SIMULACIÓN DE COPIA {name} ({source_size} bytes)")
                continue
            with source.open(name, "rb") as source_file:
                saved_name = destination.save(name, source_file)
            if saved_name != name or not destination.exists(name):
                raise OSError("El destino no conservó la key esperada.")
            if destination.size(name) != source_size:
                raise OSError("El tamaño del destino no coincide con el origen.")
            counters["copied"] += 1
            counters["bytes"] += source_size
            if stdout:
                stdout.write(f"COPIADO {name}")
        except Exception:
            counters["failed"] += 1
            logger.exception("Media migration object failed name=%s", name)
            if stdout:
                stdout.write(f"FALLIDO {name}")
    return counters


class Command(BaseCommand):
    help = "Copia los archivos locales al almacenamiento predeterminado sin borrar el origen."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--gallery", dest="gallery_uuid")
        parser.add_argument("--source-root", default=str(settings.MEDIA_ROOT))

    def handle(self, *args, **options):
        self.stdout.write(f"Modo: {'SIMULACIÓN' if options['dry_run'] else 'COPIA'}")
        photos = Photo.objects.order_by("id")
        gallery_uuid = options.get("gallery_uuid")
        if gallery_uuid:
            try:
                gallery = Gallery.objects.get(uuid=gallery_uuid)
            except (Gallery.DoesNotExist, ValueError) as exc:
                raise CommandError("La galería indicada no existe.") from exc
            photos = photos.filter(gallery=gallery)
        source = FileSystemStorage(location=options["source_root"])
        counters = copy_media_objects(
            source=source,
            destination=default_storage,
            names=photo_object_names(photos),
            dry_run=options["dry_run"],
            stdout=self.stdout,
        )
        self.stdout.write(f"Copiados: {counters['copied']}")
        self.stdout.write(f"Omitidos: {counters['skipped']}")
        self.stdout.write(f"Fallidos: {counters['failed']}")
        self.stdout.write(f"Bytes transferidos: {counters['bytes']}")
        logger.info(
            "Media migration summary copied=%s skipped=%s failed=%s bytes=%s dry_run=%s",
            counters["copied"],
            counters["skipped"],
            counters["failed"],
            counters["bytes"],
            options["dry_run"],
        )
