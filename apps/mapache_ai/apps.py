from django.apps import AppConfig


class MapacheAIConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.mapache_ai"
    verbose_name = "Mapache AI"

    def ready(self) -> None:
        from . import signals  # noqa: F401
