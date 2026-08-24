from django.db import models


class ContactInquiry(models.Model):
    class Service(models.TextChoices):
        SPORTS = "SPORTS", "Fotografía deportiva"
        VIDEO = "VIDEO", "Producción audiovisual"
        COMMERCIAL = "COMMERCIAL", "Fotografía comercial"
        DRONE = "DRONE", "Cobertura aérea"
        DIGITAL = "DIGITAL", "Contenido digital"
        PLATFORM = "PLATFORM", "Galerías y Mapache AI"
        OTHER = "OTHER", "Otro proyecto"

    name = models.CharField("Nombre", max_length=120)
    email = models.EmailField("Correo electrónico")
    phone = models.CharField("Teléfono", max_length=40, blank=True)
    service = models.CharField("Servicio", max_length=20, choices=Service.choices)
    message = models.TextField("Mensaje", max_length=3000)
    created_at = models.DateTimeField("Recibido", auto_now_add=True)
    is_read = models.BooleanField("Leído", default=False)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Consulta de contacto"
        verbose_name_plural = "Consultas de contacto"

    def __str__(self):
        return f"{self.name} · {self.get_service_display()}"
