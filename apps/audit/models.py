from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="usuario",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="audit_logs",
    )
    action = models.CharField("acción", max_length=100)
    model_name = models.CharField("tipo de registro", max_length=100)
    object_id = models.CharField("identificador del registro", max_length=255, blank=True)
    metadata = models.JSONField("datos adicionales", default=dict, blank=True)
    created_at = models.DateTimeField("creado", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "registro de auditoría"
        verbose_name_plural = "registros de auditoría"

    def __str__(self) -> str:
        return f"{self.action} · {self.model_name} · {self.object_id or 'sistema'}"
