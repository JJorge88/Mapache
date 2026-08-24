from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Administrador"
        STAFF = "STAFF", "Personal"

    role = models.CharField("rol", max_length=10, choices=Role.choices, default=Role.STAFF)
    avatar = models.ImageField(
        "imagen de perfil", upload_to="avatars/%Y/%m/", blank=True, null=True
    )
    is_staff = models.BooleanField(
        "acceso al panel administrativo",
        default=False,
        help_text="Indica si el usuario puede acceder al panel administrativo.",
    )

    def save(self, *args, **kwargs):
        if self.is_superuser:
            self.role = self.Role.ADMIN
            self.is_staff = True
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.get_full_name() or self.username
