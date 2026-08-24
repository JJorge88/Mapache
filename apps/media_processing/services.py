import logging
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.audit.models import AuditLog
from apps.galleries.models import Gallery, Photo
from apps.galleries.services import add_photo

from .exceptions import PermanentImageError, TransientProcessingError

logger = logging.getLogger("mapache.media_processing")

FORMAT_DETAILS = {
    "JPEG": {"extensions": {".jpg", ".jpeg"}, "mime": "image/jpeg", "suffix": ".jpg"},
    "PNG": {"extensions": {".png"}, "mime": "image/png", "suffix": ".png"},
    "WEBP": {"extensions": {".webp"}, "mime": "image/webp", "suffix": ".webp"},
}


@dataclass(frozen=True)
class ValidatedImage:
    format: str
    mime_type: str
    suffix: str
    file_size: int


def _file_size(uploaded_file) -> int:
    size = getattr(uploaded_file, "size", None)
    if size is not None:
        return size
    position = uploaded_file.tell()
    uploaded_file.seek(0, 2)
    size = uploaded_file.tell()
    uploaded_file.seek(position)
    return size


def validate_image_file(uploaded_file) -> ValidatedImage:
    original_name = Path(getattr(uploaded_file, "name", "")).name
    extension = Path(original_name).suffix.lower()
    allowed_extensions = {
        extension for item in FORMAT_DETAILS.values() for extension in item["extensions"]
    }
    if extension not in allowed_extensions:
        raise ValidationError("Formato no permitido. Usa JPEG, PNG o WebP.")
    size = _file_size(uploaded_file)
    max_bytes = settings.MAPACHE_MAX_PHOTO_SIZE_MB * 1024 * 1024
    if size > max_bytes:
        raise ValidationError(
            f"La fotografía supera el límite de {settings.MAPACHE_MAX_PHOTO_SIZE_MB} MB."
        )
    try:
        uploaded_file.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(uploaded_file) as image:
                image_format = (image.format or "").upper()
                if image_format not in FORMAT_DETAILS:
                    raise ValidationError("El contenido no corresponde a un formato admitido.")
                details = FORMAT_DETAILS[image_format]
                if extension not in details["extensions"]:
                    raise ValidationError("La extensión no coincide con el contenido de la imagen.")
                if getattr(image, "is_animated", False) and getattr(image, "n_frames", 1) > 1:
                    raise ValidationError("Las imágenes animadas no están admitidas.")
                image.verify()
    except ValidationError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValidationError("El archivo no es una imagen válida o está dañado.") from exc
    finally:
        uploaded_file.seek(0)
    return ValidatedImage(
        format=image_format,
        mime_type=details["mime"],
        suffix=details["suffix"],
        file_size=size,
    )


@transaction.atomic
def upload_photo(*, gallery: Gallery, uploaded_file, uploaded_by) -> Photo:
    validated = validate_image_file(uploaded_file)
    original_filename = Path(uploaded_file.name).name
    photo = add_photo(
        gallery=gallery,
        original_file=uploaded_file,
        uploaded_by=uploaded_by,
        filename=f"upload{validated.suffix}",
        original_filename=original_filename,
        mime_type=validated.mime_type,
        file_size=validated.file_size,
    )
    photo.filename = Path(photo.original_file.name).name
    photo.processing_status = Photo.ProcessingStatus.PENDING
    photo.save(update_fields=["filename", "processing_status", "updated_at"])

    from .tasks import process_photo

    transaction.on_commit(lambda photo_id=photo.pk: process_photo.delay(photo_id))
    return photo


def _normalized_image(photo: Photo) -> tuple[Image.Image, str]:
    try:
        with photo.original_file.storage.open(photo.original_file.name, "rb") as source:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(source) as opened:
                    image_format = (opened.format or "").upper()
                    if image_format not in FORMAT_DETAILS:
                        raise PermanentImageError("Formato de imagen no admitido.")
                    if getattr(opened, "is_animated", False) and getattr(opened, "n_frames", 1) > 1:
                        raise PermanentImageError("Las imágenes animadas no están admitidas.")
                    normalized = ImageOps.exif_transpose(opened)
                    normalized.load()
                    return normalized.copy(), image_format
    except PermanentImageError:
        raise
    except (UnidentifiedImageError, SyntaxError, Image.DecompressionBombError) as exc:
        raise PermanentImageError(
            "La imagen original está dañada o no puede decodificarse."
        ) from exc
    except Image.DecompressionBombWarning as exc:
        raise PermanentImageError("La imagen excede los límites seguros de dimensiones.") from exc
    except OSError as exc:
        if photo.original_file.storage.exists(photo.original_file.name):
            raise PermanentImageError(
                "La imagen original está dañada o no puede decodificarse."
            ) from exc
        raise TransientProcessingError("No fue posible acceder temporalmente al original.") from exc


def _web_safe_image(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        return image.convert("RGBA")
    if image.mode != "RGB":
        return image.convert("RGB")
    return image.copy()


def _webp_bytes(image: Image.Image, max_dimension: int) -> bytes:
    derivative = _web_safe_image(image)
    derivative.thumbnail(
        (max_dimension, max_dimension),
        Image.Resampling.LANCZOS,
    )
    output = BytesIO()
    derivative.save(
        output,
        format="WEBP",
        quality=settings.MAPACHE_IMAGE_WEBP_QUALITY,
        method=6,
    )
    derivative.close()
    return output.getvalue()


def _delete_field_file(field_file) -> None:
    if field_file.name:
        try:
            field_file.storage.delete(field_file.name)
        except Exception:  # Storage backends use provider-specific exception types.
            logger.exception("Could not delete derivative %s", field_file.name)


def delete_photo_derivatives(photo: Photo) -> None:
    _delete_field_file(photo.optimized_file)
    _delete_field_file(photo.thumbnail_file)
    photo.optimized_file = ""
    photo.thumbnail_file = ""


def _save_derivative(photo: Photo, field_name: str, content: bytes) -> None:
    field_file = getattr(photo, field_name)
    generated_name = field_file.field.generate_filename(photo, f"{photo.uuid}.webp")
    field_file.storage.delete(generated_name)
    field_file.save(f"{photo.uuid}.webp", ContentFile(content), save=False)


def _orientation(width: int, height: int) -> str:
    if width > height:
        return Photo.Orientation.LANDSCAPE
    if height > width:
        return Photo.Orientation.PORTRAIT
    return Photo.Orientation.SQUARE


def _processing_failed(photo: Photo, message: str) -> None:
    sanitized = " ".join(message.split())[:500]
    delete_photo_derivatives(photo)
    photo.processing_status = Photo.ProcessingStatus.ERROR
    photo.processing_error = sanitized
    photo.processed_at = None
    photo.save(
        update_fields=[
            "optimized_file",
            "thumbnail_file",
            "processing_status",
            "processing_error",
            "processed_at",
            "updated_at",
        ]
    )
    AuditLog.objects.create(
        user=photo.uploaded_by,
        action="PHOTO_PROCESSING_FAILED",
        model_name="Gallery",
        object_id=str(photo.gallery.uuid),
        metadata={"photo_uuid": str(photo.uuid)},
    )


def process_photo_image(photo_id: int) -> Photo | None:
    try:
        photo = Photo.objects.select_related("gallery", "uploaded_by").get(pk=photo_id)
    except Photo.DoesNotExist:
        logger.info("Photo %s no longer exists; processing skipped", photo_id)
        return None
    logger.info("Starting processing for photo %s", photo.uuid)
    photo.processing_status = Photo.ProcessingStatus.PROCESSING
    photo.processing_error = ""
    photo.processed_at = None
    photo.save(
        update_fields=["processing_status", "processing_error", "processed_at", "updated_at"]
    )
    try:
        image, image_format = _normalized_image(photo)
        width, height = image.size
        try:
            optimized = _webp_bytes(image, settings.MAPACHE_OPTIMIZED_MAX_DIMENSION)
            thumbnail = _webp_bytes(image, settings.MAPACHE_THUMBNAIL_MAX_DIMENSION)
        except (OSError, ValueError) as exc:
            raise PermanentImageError("No fue posible convertir la imagen a WebP.") from exc
        image.close()
        delete_photo_derivatives(photo)
        try:
            _save_derivative(photo, "optimized_file", optimized)
            _save_derivative(photo, "thumbnail_file", thumbnail)
        except Exception as exc:
            raise TransientProcessingError(
                "No fue posible guardar temporalmente los derivados."
            ) from exc
        photo.width = width
        photo.height = height
        photo.orientation = _orientation(width, height)
        photo.mime_type = FORMAT_DETAILS[image_format]["mime"]
        try:
            photo.file_size = photo.original_file.storage.size(photo.original_file.name)
        except Exception as exc:
            raise TransientProcessingError(
                "No fue posible consultar temporalmente el archivo original."
            ) from exc
        photo.processing_status = Photo.ProcessingStatus.READY
        photo.processing_error = ""
        photo.processed_at = timezone.now()
        photo.save(
            update_fields=[
                "optimized_file",
                "thumbnail_file",
                "width",
                "height",
                "orientation",
                "mime_type",
                "file_size",
                "processing_status",
                "processing_error",
                "processed_at",
                "updated_at",
            ]
        )
    except PermanentImageError as exc:
        logger.warning("Permanent processing failure for photo %s", photo.uuid, exc_info=True)
        _processing_failed(photo, str(exc))
        raise
    except TransientProcessingError as exc:
        logger.exception("Transient processing failure for photo %s", photo.uuid)
        _processing_failed(photo, str(exc))
        raise
    except Exception as exc:
        logger.exception("Unexpected processing failure for photo %s", photo.uuid)
        transient = TransientProcessingError("Ocurrió un fallo temporal durante el procesamiento.")
        _processing_failed(photo, str(transient))
        raise transient from exc
    logger.info("Finished processing photo %s", photo.uuid)
    return photo


@transaction.atomic
def reprocess_photo(*, photo: Photo, requested_by) -> Photo:
    delete_photo_derivatives(photo)
    photo.processing_status = Photo.ProcessingStatus.PENDING
    photo.processing_error = ""
    photo.processed_at = None
    photo.save(
        update_fields=[
            "optimized_file",
            "thumbnail_file",
            "processing_status",
            "processing_error",
            "processed_at",
            "updated_at",
        ]
    )
    AuditLog.objects.create(
        user=requested_by,
        action="PHOTO_REPROCESS_REQUESTED",
        model_name="Gallery",
        object_id=str(photo.gallery.uuid),
        metadata={"photo_uuid": str(photo.uuid)},
    )
    from .tasks import process_photo

    transaction.on_commit(lambda photo_id=photo.pk: process_photo.delay(photo_id))
    return photo
