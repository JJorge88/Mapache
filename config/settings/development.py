from .base import *  # noqa: F403

DEBUG = env.bool("DJANGO_DEBUG", default=True)  # noqa: F405
ALLOWED_HOSTS = env.list(  # noqa: F405
    "DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"]
)
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
PUBLIC_SITE_URL = env.str(  # noqa: F405
    "PUBLIC_SITE_URL", default="http://127.0.0.1:8003"
).rstrip("/")
