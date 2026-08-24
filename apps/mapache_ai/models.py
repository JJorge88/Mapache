from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from pgvector.django import HnswIndex, VectorField

from apps.core.models import TimestampedModel, UUIDModel
from apps.galleries.models import Gallery, Photo

from .constants import FACE_EMBEDDING_DIMENSION


class GalleryAISettings(TimestampedModel):
    class BibFormat(models.TextChoices):
        NUMERIC = "NUMERIC", "Numérico"
        ALPHANUMERIC = "ALPHANUMERIC", "Alfanumérico"

    class IndexingStatus(models.TextChoices):
        DISABLED = "DISABLED", "Desactivado"
        PENDING = "PENDING", "Preparando"
        INDEXING = "INDEXING", "Indexando"
        READY = "READY", "Listo"
        ERROR = "ERROR", "Error"

    gallery = models.OneToOneField(
        Gallery,
        verbose_name="galería",
        on_delete=models.CASCADE,
        related_name="ai_settings",
    )
    enabled = models.BooleanField("activado", default=False)
    face_search_enabled = models.BooleanField("búsqueda por rostro", default=False)
    bib_search_enabled = models.BooleanField("búsqueda por número", default=False)
    bib_format = models.CharField(
        "formato del dorsal", max_length=16, choices=BibFormat.choices, default=BibFormat.NUMERIC
    )
    bib_min_length = models.PositiveSmallIntegerField("longitud mínima", default=1)
    bib_max_length = models.PositiveSmallIntegerField("longitud máxima", default=6)
    indexing_status = models.CharField(
        "estado de indexación",
        max_length=12,
        choices=IndexingStatus.choices,
        default=IndexingStatus.DISABLED,
        db_index=True,
    )
    indexed_photos = models.PositiveIntegerField("fotografías indexadas", default=0)
    total_photos = models.PositiveIntegerField("fotografías totales", default=0)
    last_indexed_at = models.DateTimeField("última indexación", blank=True, null=True)
    face_indexing_status = models.CharField(
        "estado del índice facial",
        max_length=12,
        choices=IndexingStatus.choices,
        default=IndexingStatus.DISABLED,
        db_index=True,
    )
    face_indexed_photos = models.PositiveIntegerField("fotografías con índice facial", default=0)
    face_total_photos = models.PositiveIntegerField("fotografías faciales totales", default=0)
    face_last_indexed_at = models.DateTimeField("última indexación facial", blank=True, null=True)
    bib_indexing_status = models.CharField(
        "estado del índice de dorsales",
        max_length=12,
        choices=IndexingStatus.choices,
        default=IndexingStatus.DISABLED,
        db_index=True,
    )
    bib_indexed_photos = models.PositiveIntegerField(
        "fotografías con dorsales indexados", default=0
    )
    bib_total_photos = models.PositiveIntegerField("fotografías de dorsales totales", default=0)
    bib_last_indexed_at = models.DateTimeField(
        "última indexación de dorsales", blank=True, null=True
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(bib_min_length__gte=1), name="bib_min_length_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(bib_max_length__gte=models.F("bib_min_length")),
                name="bib_length_range_valid",
            ),
        ]
        verbose_name = "configuración de Mapache AI"
        verbose_name_plural = "configuraciones de Mapache AI"

    def __str__(self) -> str:
        return f"Mapache AI · {self.gallery}"

    def clean(self) -> None:
        super().clean()
        if self.bib_min_length < 1 or self.bib_max_length < self.bib_min_length:
            raise ValidationError("El rango de longitud del dorsal no es válido.")
        if self.bib_max_length > 16:
            raise ValidationError("La longitud máxima del dorsal no puede superar 16.")


class PhotoFaceIndex(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        INDEXING = "INDEXING", "Indexando"
        READY = "READY", "Lista"
        ERROR = "ERROR", "Error"

    gallery = models.ForeignKey(
        Gallery,
        verbose_name="galería",
        on_delete=models.CASCADE,
        related_name="photo_face_indexes",
    )
    photo = models.OneToOneField(
        Photo,
        verbose_name="fotografía",
        on_delete=models.CASCADE,
        related_name="face_index_state",
    )
    status = models.CharField(
        "estado", max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    face_count = models.PositiveIntegerField("cantidad de rostros", default=0)
    error = models.CharField("error", max_length=500, blank=True)
    indexed_at = models.DateTimeField("indexada", blank=True, null=True)

    class Meta:
        indexes = [models.Index(fields=["gallery", "status"])]
        verbose_name = "estado de índice facial"
        verbose_name_plural = "estados de índice facial"

    def __str__(self) -> str:
        return f"Índice facial · {self.photo}"

    def clean(self) -> None:
        super().clean()
        if self.photo_id and self.gallery_id != self.photo.gallery_id:
            raise ValidationError("La fotografía y el índice deben pertenecer a la misma galería.")


class FaceEmbedding(UUIDModel, models.Model):
    gallery = models.ForeignKey(
        Gallery,
        verbose_name="galería",
        on_delete=models.CASCADE,
        related_name="face_embeddings",
    )
    photo = models.ForeignKey(
        Photo,
        verbose_name="fotografía",
        on_delete=models.CASCADE,
        related_name="face_embeddings",
    )
    face_index = models.PositiveIntegerField("número de rostro")
    embedding = VectorField("representación facial", dimensions=FACE_EMBEDDING_DIMENSION)
    confidence = models.FloatField("confianza")
    bounding_box = models.JSONField("recuadro del rostro")
    created_at = models.DateTimeField("creado", auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["photo", "face_index"], name="unique_face_index_per_photo"
            ),
            models.CheckConstraint(
                condition=models.Q(face_index__gte=0), name="face_index_nonnegative"
            ),
            models.CheckConstraint(
                condition=models.Q(confidence__gte=0.0) & models.Q(confidence__lte=1.0),
                name="face_confidence_range",
            ),
        ]
        indexes = [
            models.Index(fields=["gallery", "photo"]),
            HnswIndex(
                name="face_embedding_cos_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]
        verbose_name = "representación facial"
        verbose_name_plural = "representaciones faciales"

    def __str__(self) -> str:
        return f"Rostro {self.face_index} · {self.photo}"

    def clean(self) -> None:
        super().clean()
        if self.photo_id and self.gallery_id != self.photo.gallery_id:
            raise ValidationError("El rostro y la fotografía deben pertenecer a la misma galería.")


class FaceSearchSession(UUIDModel, models.Model):
    class Status(models.TextChoices):
        PROCESSING = "PROCESSING", "Procesando"
        COMPLETED = "COMPLETED", "Completada"
        ERROR = "ERROR", "Error"

    gallery = models.ForeignKey(
        Gallery,
        verbose_name="galería",
        on_delete=models.CASCADE,
        related_name="face_search_sessions",
    )
    consent_version = models.CharField("versión del consentimiento", max_length=20)
    consented_at = models.DateTimeField("consentimiento otorgado")
    status = models.CharField("estado", max_length=12, choices=Status.choices)
    results_count = models.PositiveIntegerField("cantidad de resultados", default=0)
    created_at = models.DateTimeField("creada", auto_now_add=True)
    expires_at = models.DateTimeField("expira", db_index=True)

    class Meta:
        indexes = [models.Index(fields=["gallery", "expires_at"])]
        verbose_name = "sesión de búsqueda facial"
        verbose_name_plural = "sesiones de búsqueda facial"

    def __str__(self) -> str:
        return f"Búsqueda {self.uuid} · {self.gallery}"

    @classmethod
    def new_expiration(cls):
        return timezone.now() + timedelta(seconds=settings.MAPACHE_FACE_SEARCH_SESSION_TTL)

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()


class DetectedBib(UUIDModel, models.Model):
    gallery = models.ForeignKey(
        Gallery,
        verbose_name="galería",
        on_delete=models.CASCADE,
        related_name="detected_bibs",
    )
    photo = models.ForeignKey(
        Photo,
        verbose_name="fotografía",
        on_delete=models.CASCADE,
        related_name="detected_bibs",
    )
    raw_text = models.CharField("texto detectado", max_length=64)
    normalized_number = models.CharField("número normalizado", max_length=16)
    confidence = models.FloatField("confianza")
    bounding_box = models.JSONField("recuadro del dorsal")
    created_at = models.DateTimeField("creado", auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(confidence__gte=0.0) & models.Q(confidence__lte=1.0),
                name="bib_confidence_range",
            )
        ]
        indexes = [
            models.Index(fields=["gallery", "normalized_number"], name="bib_gallery_number_idx"),
            models.Index(fields=["photo"], name="bib_photo_idx"),
        ]
        verbose_name = "dorsal detectado"
        verbose_name_plural = "dorsales detectados"

    def __str__(self) -> str:
        return f"#{self.normalized_number} · {self.photo}"

    def clean(self) -> None:
        super().clean()
        if self.photo_id and self.gallery_id != self.photo.gallery_id:
            raise ValidationError("El dorsal y la fotografía deben pertenecer a la misma galería.")


class BibPhotoAnalysis(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        INDEXING = "INDEXING", "Indexando"
        READY = "READY", "Lista"
        ERROR = "ERROR", "Error"

    gallery = models.ForeignKey(
        Gallery,
        verbose_name="galería",
        on_delete=models.CASCADE,
        related_name="bib_photo_analyses",
    )
    photo = models.OneToOneField(
        Photo,
        verbose_name="fotografía",
        on_delete=models.CASCADE,
        related_name="bib_analysis_state",
    )
    status = models.CharField(
        "estado", max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    detected_count = models.PositiveIntegerField("dorsales detectados", default=0)
    error = models.CharField("error", max_length=500, blank=True)
    processed_at = models.DateTimeField("procesada", blank=True, null=True)

    class Meta:
        indexes = [models.Index(fields=["gallery", "status"], name="bib_analysis_status_idx")]
        verbose_name = "análisis de dorsales"
        verbose_name_plural = "análisis de dorsales"

    def clean(self) -> None:
        super().clean()
        if self.photo_id and self.gallery_id != self.photo.gallery_id:
            raise ValidationError(
                "El análisis y la fotografía deben pertenecer a la misma galería."
            )


class BibSearchSession(UUIDModel, models.Model):
    gallery = models.ForeignKey(
        Gallery,
        verbose_name="galería",
        on_delete=models.CASCADE,
        related_name="bib_search_sessions",
    )
    normalized_number = models.CharField("número normalizado", max_length=16)
    results_count = models.PositiveIntegerField("cantidad de resultados", default=0)
    created_at = models.DateTimeField("creada", auto_now_add=True)
    expires_at = models.DateTimeField("expira", db_index=True)

    class Meta:
        indexes = [models.Index(fields=["gallery", "expires_at"], name="bib_session_expiry_idx")]
        verbose_name = "sesión de búsqueda por número"
        verbose_name_plural = "sesiones de búsqueda por número"

    def __str__(self) -> str:
        return f"Búsqueda #{self.normalized_number} · {self.gallery}"

    @classmethod
    def new_expiration(cls):
        return timezone.now() + timedelta(seconds=settings.MAPACHE_BIB_SEARCH_SESSION_TTL)

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()


class CombinedSearchSession(UUIDModel, models.Model):
    class Status(models.TextChoices):
        PROCESSING = "PROCESSING", "Procesando"
        COMPLETED = "COMPLETED", "Completada"
        ERROR = "ERROR", "Error"

    gallery = models.ForeignKey(
        Gallery,
        verbose_name="galería",
        on_delete=models.CASCADE,
        related_name="combined_search_sessions",
    )
    normalized_number = models.CharField("número normalizado", max_length=16)
    consent_version = models.CharField("versión del consentimiento", max_length=20)
    consented_at = models.DateTimeField("consentimiento otorgado")
    status = models.CharField("estado", max_length=12, choices=Status.choices)
    results_count = models.PositiveIntegerField("cantidad de resultados", default=0)
    face_results_count = models.PositiveIntegerField("resultados por rostro", default=0)
    bib_results_count = models.PositiveIntegerField("resultados por número", default=0)
    agreement_results_count = models.PositiveIntegerField("resultados coincidentes", default=0)
    created_at = models.DateTimeField("creada", auto_now_add=True)
    expires_at = models.DateTimeField("expira", db_index=True)

    class Meta:
        indexes = [models.Index(fields=["gallery", "expires_at"], name="combined_session_exp_idx")]
        verbose_name = "sesión de búsqueda combinada"
        verbose_name_plural = "sesiones de búsqueda combinada"

    def __str__(self) -> str:
        return f"Búsqueda combinada #{self.normalized_number} · {self.gallery}"

    @classmethod
    def new_expiration(cls):
        return timezone.now() + timedelta(seconds=settings.MAPACHE_COMBINED_SEARCH_SESSION_TTL)

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()
