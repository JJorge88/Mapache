import re
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TimestampedModel, UUIDModel

PIN_PATTERN = re.compile(r"^\d{4,8}$")


def gallery_upload_path(instance, filename: str) -> str:
    extension = Path(filename).suffix.lower()
    if extension == ".jpeg":
        extension = ".jpg"
    return f"galleries/{instance.gallery.uuid}/originals/{instance.uuid}{extension}"


def optimized_upload_path(instance, _filename: str) -> str:
    return f"galleries/{instance.gallery.uuid}/optimized/{instance.uuid}.webp"


def thumbnail_upload_path(instance, _filename: str) -> str:
    return f"galleries/{instance.gallery.uuid}/thumbnails/{instance.uuid}.webp"


def gallery_download_upload_path(instance, _filename: str) -> str:
    return f"downloads/galleries/{instance.gallery.uuid}/{instance.uuid}.zip"


class Gallery(UUIDModel, TimestampedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Borrador"
        PUBLISHED = "PUBLISHED", "Publicada"
        ARCHIVED = "ARCHIVED", "Archivada"

    class Visibility(models.TextChoices):
        PUBLIC = "PUBLIC", "Pública"
        PRIVATE_PIN = "PRIVATE_PIN", "Privada con PIN"
        UNLISTED = "UNLISTED", "No listada"

    title = models.CharField("título", max_length=200)
    slug = models.SlugField("identificador web", max_length=220, unique=True)
    description = models.TextField("descripción", blank=True)
    event_date = models.DateField("fecha del evento", blank=True, null=True, db_index=True)
    status = models.CharField(
        "estado", max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    visibility = models.CharField(
        "visibilidad",
        max_length=12,
        choices=Visibility.choices,
        default=Visibility.PUBLIC,
        db_index=True,
    )
    pin_hash = models.CharField("protección del PIN", max_length=128, blank=True)
    allow_photo_download = models.BooleanField("permitir descarga individual", default=False)
    allow_gallery_download = models.BooleanField("permitir descarga completa", default=False)
    show_in_portfolio = models.BooleanField("mostrar en el portafolio", default=False)
    is_featured = models.BooleanField("galería destacada", default=False)
    published_at = models.DateTimeField("publicada", blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="creada por",
        on_delete=models.PROTECT,
        related_name="created_galleries",
    )
    cover_photo = models.ForeignKey(
        "Photo",
        verbose_name="fotografía de portada",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="cover_for_galleries",
    )

    class Meta:
        ordering = ["-event_date", "-created_at"]
        verbose_name = "galería"
        verbose_name_plural = "galerías"

    def __str__(self) -> str:
        return self.title

    @property
    def has_pin(self) -> bool:
        return bool(self.pin_hash)

    def set_pin(self, pin: str) -> None:
        if not isinstance(pin, str) or not PIN_PATTERN.fullmatch(pin):
            raise ValidationError({"pin": "El PIN debe contener entre 4 y 8 dígitos."})
        self.pin_hash = make_password(pin)

    def check_pin(self, pin: str) -> bool:
        return bool(self.pin_hash) and check_password(pin, self.pin_hash)

    def clean(self) -> None:
        super().clean()
        if (
            self.status == self.Status.PUBLISHED
            and self.visibility == self.Visibility.PRIVATE_PIN
            and not self.has_pin
        ):
            raise ValidationError(
                {"visibility": "Una galería privada necesita un PIN antes de publicarse."}
            )
        if self.cover_photo_id and self.cover_photo.gallery_id != self.pk:
            raise ValidationError({"cover_photo": "La portada debe pertenecer a esta galería."})


class Photo(UUIDModel, TimestampedModel):
    class Orientation(models.TextChoices):
        LANDSCAPE = "LANDSCAPE", "Horizontal"
        PORTRAIT = "PORTRAIT", "Vertical"
        SQUARE = "SQUARE", "Cuadrada"
        UNKNOWN = "UNKNOWN", "Desconocida"

    class ProcessingStatus(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        PROCESSING = "PROCESSING", "Procesando"
        READY = "READY", "Lista"
        ERROR = "ERROR", "Error"

    gallery = models.ForeignKey(
        Gallery, verbose_name="galería", on_delete=models.CASCADE, related_name="photos"
    )
    original_file = models.FileField("archivo original", upload_to=gallery_upload_path)
    optimized_file = models.FileField(
        "archivo optimizado", upload_to=optimized_upload_path, blank=True
    )
    thumbnail_file = models.FileField("miniatura", upload_to=thumbnail_upload_path, blank=True)
    filename = models.CharField("nombre del archivo", max_length=255)
    original_filename = models.CharField("nombre original", max_length=255)
    mime_type = models.CharField("tipo de contenido", max_length=100, blank=True)
    file_size = models.PositiveBigIntegerField("tamaño", blank=True, null=True)
    width = models.PositiveIntegerField("ancho", blank=True, null=True)
    height = models.PositiveIntegerField("alto", blank=True, null=True)
    orientation = models.CharField(
        "orientación",
        max_length=12,
        choices=Orientation.choices,
        default=Orientation.UNKNOWN,
    )
    sort_order = models.PositiveIntegerField("orden", default=0)
    processing_status = models.CharField(
        "estado de procesamiento",
        max_length=12,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.PENDING,
    )
    processing_error = models.CharField("error de procesamiento", max_length=500, blank=True)
    processed_at = models.DateTimeField("procesada", blank=True, null=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="subida por",
        on_delete=models.PROTECT,
        related_name="uploaded_photos",
    )

    class Meta:
        ordering = ["sort_order", "created_at"]
        indexes = [models.Index(fields=["gallery", "sort_order"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(sort_order__gte=0),
                name="photo_sort_order_nonnegative",
            )
        ]
        verbose_name = "fotografía"
        verbose_name_plural = "fotografías"

    def __str__(self) -> str:
        return self.original_filename or self.filename


class GalleryDownload(UUIDModel, TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        PROCESSING = "PROCESSING", "Procesando"
        READY = "READY", "Lista"
        ERROR = "ERROR", "Error"
        EXPIRED = "EXPIRED", "Expirada"

    gallery = models.ForeignKey(
        Gallery,
        verbose_name="galería",
        on_delete=models.CASCADE,
        related_name="downloads",
    )
    status = models.CharField(
        "estado",
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    file = models.FileField("archivo", upload_to=gallery_download_upload_path, blank=True)
    photo_count = models.PositiveIntegerField("cantidad de fotografías", default=0)
    processed_photos = models.PositiveIntegerField("fotografías procesadas", default=0)
    file_size = models.PositiveBigIntegerField("tamaño", default=0)
    content_fingerprint = models.CharField("huella del contenido", max_length=64, db_index=True)
    authorization_hash = models.CharField("huella de autorización", max_length=64, db_index=True)
    requested_at = models.DateTimeField("solicitada", auto_now_add=True)
    started_at = models.DateTimeField("iniciada", blank=True, null=True)
    completed_at = models.DateTimeField("completada", blank=True, null=True)
    expires_at = models.DateTimeField("expira", blank=True, null=True, db_index=True)
    error = models.CharField("error", max_length=500, blank=True)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [models.Index(fields=["gallery", "status", "expires_at"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(processed_photos__lte=models.F("photo_count")),
                name="gallery_download_progress_lte_total",
            ),
            models.UniqueConstraint(
                fields=["gallery", "content_fingerprint", "authorization_hash"],
                condition=models.Q(status__in=["PENDING", "PROCESSING", "READY"]),
                name="unique_active_gallery_download",
            ),
        ]
        verbose_name = "descarga de galería"
        verbose_name_plural = "descargas de galerías"

    def __str__(self) -> str:
        return f"{self.gallery} · {self.get_status_display()}"


class GalleryUploadBatch(UUIDModel, TimestampedModel):
    class Status(models.TextChoices):
        CREATED = "CREATED", "Creada"
        UPLOADING = "UPLOADING", "Subiendo"
        PROCESSING = "PROCESSING", "Procesando"
        COMPLETED = "COMPLETED", "Completada"
        PARTIAL = "PARTIAL", "Parcial"
        ABORTED = "ABORTED", "Abortada"
        EXPIRED = "EXPIRED", "Expirada"
        ERROR = "ERROR", "Error"

    gallery = models.ForeignKey(
        Gallery,
        verbose_name="galería",
        on_delete=models.CASCADE,
        related_name="upload_batches",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="creado por",
        on_delete=models.PROTECT,
        related_name="gallery_upload_batches",
    )
    status = models.CharField(
        "estado", max_length=12, choices=Status.choices, default=Status.CREATED, db_index=True
    )
    total_files = models.PositiveIntegerField("archivos totales", default=0)
    completed_files = models.PositiveIntegerField("archivos completados", default=0)
    failed_files = models.PositiveIntegerField("archivos con error", default=0)
    total_bytes = models.PositiveBigIntegerField("tamaño total", default=0)
    uploaded_bytes = models.PositiveBigIntegerField("tamaño subido", default=0)
    expires_at = models.DateTimeField("expira", db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["gallery", "created_by", "status"])]
        verbose_name = "lote de carga"
        verbose_name_plural = "lotes de carga"

    def __str__(self) -> str:
        return f"{self.gallery} · {self.get_status_display()}"


class GalleryUploadItem(UUIDModel, TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Esperando"
        UPLOADING = "UPLOADING", "Subiendo"
        UPLOADED = "UPLOADED", "Subida completa"
        CONFIRMED = "CONFIRMED", "Confirmada"
        PROCESSING = "PROCESSING", "Procesando"
        READY = "READY", "Lista"
        ERROR = "ERROR", "Error"
        ABORTED = "ABORTED", "Abortada"
        EXPIRED = "EXPIRED", "Expirada"

    class UploadMode(models.TextChoices):
        SINGLE = "SINGLE", "Carga única"
        MULTIPART = "MULTIPART", "Carga por partes"

    batch = models.ForeignKey(
        GalleryUploadBatch,
        verbose_name="lote",
        on_delete=models.CASCADE,
        related_name="items",
    )
    gallery = models.ForeignKey(
        Gallery,
        verbose_name="galería",
        on_delete=models.CASCADE,
        related_name="upload_items",
    )
    reserved_photo_uuid = models.UUIDField(
        "identificador reservado", default=uuid.uuid4, unique=True, editable=False
    )
    original_filename = models.CharField("nombre original", max_length=255)
    object_key = models.CharField("clave del objeto", max_length=700, unique=True)
    expected_size = models.PositiveBigIntegerField("tamaño esperado")
    content_type = models.CharField("tipo de contenido", max_length=100)
    last_modified = models.PositiveBigIntegerField("última modificación", blank=True, null=True)
    status = models.CharField(
        "estado", max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    upload_mode = models.CharField("modo de carga", max_length=12, choices=UploadMode.choices)
    multipart_upload_id = models.CharField(
        "identificador de carga por partes", max_length=1024, blank=True
    )
    photo = models.OneToOneField(
        Photo,
        verbose_name="fotografía",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="direct_upload_item",
    )
    error = models.CharField("error", max_length=500, blank=True)
    completed_at = models.DateTimeField("completado", blank=True, null=True)
    expires_at = models.DateTimeField("expira", db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["batch", "status"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(expected_size__gt=0),
                name="gallery_upload_item_size_positive",
            )
        ]
        verbose_name = "archivo de carga"
        verbose_name_plural = "archivos de carga"

    def __str__(self) -> str:
        return self.original_filename
