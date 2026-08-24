from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False

if not ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS es obligatorio en producción.")
if not CELERY_BROKER_URL or not CELERY_RESULT_BACKEND:  # noqa: F405
    raise ImproperlyConfigured(
        "CELERY_BROKER_URL y CELERY_RESULT_BACKEND son obligatorios en producción."
    )
if not PUBLIC_SITE_URL:  # noqa: F405
    raise ImproperlyConfigured("PUBLIC_SITE_URL es obligatorio en producción.")
public_site = urlparse(PUBLIC_SITE_URL)  # noqa: F405
if public_site.scheme not in {"http", "https"} or not public_site.netloc:
    raise ImproperlyConfigured("PUBLIC_SITE_URL debe ser una URL absoluta HTTP(S).")
if MAPACHE_AI_ENABLED and (  # noqa: F405
    not MAPACHE_FACE_DETECTOR_MODEL.is_file()  # noqa: F405
    or not MAPACHE_FACE_RECOGNIZER_MODEL.is_file()  # noqa: F405
):
    raise ImproperlyConfigured(
        "MAPACHE_AI_ENABLED requiere los modelos YuNet y SFace configurados."
    )
if MAPACHE_AI_ENABLED and not REDIS_URL:  # noqa: F405
    raise ImproperlyConfigured(
        "MAPACHE_AI_ENABLED requiere REDIS_URL para resultados temporales y rate limiting."
    )

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
X_FRAME_OPTIONS = "DENY"

# En servidores tradicionales puede activarse un archivo rotativo. Por defecto se
# conserva stdout para que la plataforma de despliegue recopile y persista los logs.
LOG_FILE = env.str("DJANGO_LOG_FILE", default="")  # noqa: F405
if LOG_FILE:
    LOGGING["handlers"]["persistent_file"] = {  # noqa: F405
        "class": "logging.handlers.RotatingFileHandler",
        "filename": LOG_FILE,
        "maxBytes": 10 * 1024 * 1024,
        "backupCount": 5,
        "formatter": "standard",
    }
    LOGGING["root"]["handlers"].append("persistent_file")  # noqa: F405
