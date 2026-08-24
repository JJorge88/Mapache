from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured
from kombu import Queue

from apps.mapache_ai.constants import FACE_EMBEDDING_DIMENSION

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
    POSTGRES_PORT=(int, 5432),
)
environ.Env.read_env(BASE_DIR / ".env")


def required(name: str) -> str:
    value = env.str(name, default="").strip()
    if not value:
        raise ImproperlyConfigured(
            f"Falta la variable de entorno obligatoria {name}. "
            "Configúrala en .env; consulta .env.example."
        )
    return value


SECRET_KEY = required("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.accounts",
    "apps.website",
    "apps.audit",
    "apps.galleries",
    "apps.media_processing",
    "apps.mapache_ai",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": required("POSTGRES_DB"),
        "USER": required("POSTGRES_USER"),
        "PASSWORD": required("POSTGRES_PASSWORD"),
        "HOST": required("POSTGRES_HOST"),
        "PORT": env.int("POSTGRES_PORT"),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {"connect_timeout": 5},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es"
TIME_ZONE = "America/Guatemala"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGE_BACKEND = env.str("STORAGE_BACKEND", default="local").strip().lower()
R2_ACCOUNT_ID = env.str("R2_ACCOUNT_ID", default="").strip()
R2_ACCESS_KEY_ID = env.str("R2_ACCESS_KEY_ID", default="").strip()
R2_SECRET_ACCESS_KEY = env.str("R2_SECRET_ACCESS_KEY", default="").strip()
R2_BUCKET_NAME = env.str("R2_BUCKET_NAME", default="").strip()
R2_ENDPOINT_URL = env.str("R2_ENDPOINT_URL", default="").strip()
R2_CUSTOM_DOMAIN = env.str("R2_CUSTOM_DOMAIN", default="").strip().rstrip("/")
MAPACHE_PRIVATE_MEDIA_URL_TTL = env.int("MAPACHE_PRIVATE_MEDIA_URL_TTL", default=900)
MAPACHE_PUBLIC_MEDIA_URL_TTL = env.int("MAPACHE_PUBLIC_MEDIA_URL_TTL", default=3600)
MAPACHE_DOWNLOAD_URL_TTL = env.int("MAPACHE_DOWNLOAD_URL_TTL", default=900)
MAPACHE_GALLERY_DOWNLOAD_TTL = env.int("MAPACHE_GALLERY_DOWNLOAD_TTL", default=86400)
MAPACHE_GALLERY_DOWNLOAD_MAX_PHOTOS = env.int("MAPACHE_GALLERY_DOWNLOAD_MAX_PHOTOS", default=10000)
MAPACHE_GALLERY_DOWNLOAD_MAX_BYTES = env.int("MAPACHE_GALLERY_DOWNLOAD_MAX_BYTES", default=0)
MAPACHE_DIRECT_UPLOAD_ENABLED = env.bool("MAPACHE_DIRECT_UPLOAD_ENABLED", default=False)
MAPACHE_DIRECT_UPLOAD_MAX_FILES = env.int("MAPACHE_DIRECT_UPLOAD_MAX_FILES", default=5000)
MAPACHE_DIRECT_UPLOAD_MAX_TOTAL_BYTES = env.int(
    "MAPACHE_DIRECT_UPLOAD_MAX_TOTAL_BYTES", default=1024 * 1024 * 1024 * 1024
)
MAPACHE_UPLOAD_URL_TTL = env.int("MAPACHE_UPLOAD_URL_TTL", default=900)
MAPACHE_MULTIPART_UPLOAD_THRESHOLD_MB = env.int("MAPACHE_MULTIPART_UPLOAD_THRESHOLD_MB", default=25)
MAPACHE_MULTIPART_PART_SIZE_MB = env.int("MAPACHE_MULTIPART_PART_SIZE_MB", default=10)
MAPACHE_UPLOAD_CONCURRENCY = env.int("MAPACHE_UPLOAD_CONCURRENCY", default=4)
MAPACHE_UPLOAD_SESSION_TTL = env.int("MAPACHE_UPLOAD_SESSION_TTL", default=86400)

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": MEDIA_ROOT, "base_url": f"/{MEDIA_URL}"},
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
if STORAGE_BACKEND == "r2":
    missing_r2 = [
        name
        for name, value in {
            "R2_ACCOUNT_ID": R2_ACCOUNT_ID,
            "R2_ACCESS_KEY_ID": R2_ACCESS_KEY_ID,
            "R2_SECRET_ACCESS_KEY": R2_SECRET_ACCESS_KEY,
            "R2_BUCKET_NAME": R2_BUCKET_NAME,
        }.items()
        if not value
    ]
    if missing_r2:
        raise ImproperlyConfigured("STORAGE_BACKEND=r2 requiere: " + ", ".join(missing_r2))
    if not R2_ENDPOINT_URL:
        R2_ENDPOINT_URL = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    STORAGES["default"] = {
        "BACKEND": "apps.core.storage_backends.R2MediaStorage",
        "OPTIONS": {
            "bucket_name": R2_BUCKET_NAME,
            "access_key": R2_ACCESS_KEY_ID,
            "secret_key": R2_SECRET_ACCESS_KEY,
            "endpoint_url": R2_ENDPOINT_URL,
            "region_name": "auto",
        },
    }
elif STORAGE_BACKEND != "local":
    raise ImproperlyConfigured("STORAGE_BACKEND debe ser 'local' o 'r2'.")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:dashboard"
PUBLIC_SITE_URL = env.str("PUBLIC_SITE_URL", default="").rstrip("/")

REDIS_URL = env.str("REDIS_URL", default="")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        }
    }
CELERY_BROKER_URL = env.str("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env.str("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_SOFT_TIME_LIMIT = 540
CELERY_TASK_TIME_LIMIT = 600
CELERY_TASK_DEFAULT_QUEUE = "media"
CELERY_TASK_QUEUES = (Queue("media"), Queue("ai"), Queue("downloads"))
CELERY_TASK_ROUTES = {
    "apps.media_processing.tasks.*": {"queue": "media"},
    "apps.galleries.tasks.cleanup_expired_direct_uploads": {"queue": "media"},
    "apps.galleries.tasks.*": {"queue": "downloads"},
}

MAPACHE_MAX_PHOTO_SIZE_MB = env.int("MAPACHE_MAX_PHOTO_SIZE_MB", default=50)
MAPACHE_MAX_UPLOAD_FILES = env.int("MAPACHE_MAX_UPLOAD_FILES", default=500)
MAPACHE_UPLOAD_BATCH_SIZE = env.int("MAPACHE_UPLOAD_BATCH_SIZE", default=8)
DATA_UPLOAD_MAX_NUMBER_FILES = MAPACHE_MAX_UPLOAD_FILES
MAPACHE_OPTIMIZED_MAX_DIMENSION = env.int("MAPACHE_OPTIMIZED_MAX_DIMENSION", default=2400)
MAPACHE_THUMBNAIL_MAX_DIMENSION = env.int("MAPACHE_THUMBNAIL_MAX_DIMENSION", default=600)
MAPACHE_IMAGE_WEBP_QUALITY = env.int("MAPACHE_IMAGE_WEBP_QUALITY", default=86)

MAPACHE_AI_ENABLED = env.bool("MAPACHE_AI_ENABLED", default=False)
MAPACHE_FACE_ENGINE = env.str("MAPACHE_FACE_ENGINE", default="opencv_sface")
MAPACHE_FACE_EMBEDDING_DIMENSION = FACE_EMBEDDING_DIMENSION
MAPACHE_FACE_DETECTOR_MODEL = BASE_DIR / env.str(
    "MAPACHE_FACE_DETECTOR_MODEL",
    default="models/mapache_ai/face_detection_yunet_2023mar.onnx",
)
MAPACHE_FACE_RECOGNIZER_MODEL = BASE_DIR / env.str(
    "MAPACHE_FACE_RECOGNIZER_MODEL",
    default="models/mapache_ai/face_recognition_sface_2021dec.onnx",
)
MAPACHE_FACE_MATCH_THRESHOLD = env.float("MAPACHE_FACE_MATCH_THRESHOLD", default=0.363)
MAPACHE_FACE_STRONG_MATCH_THRESHOLD = env.float(
    "MAPACHE_FACE_STRONG_MATCH_THRESHOLD", default=0.45
)
MAPACHE_FACE_SEARCH_LIMIT = env.int("MAPACHE_FACE_SEARCH_LIMIT", default=100)
MAPACHE_FACE_QUERY_MAX_MB = env.int("MAPACHE_FACE_QUERY_MAX_MB", default=10)
MAPACHE_FACE_SEARCH_SESSION_TTL = env.int("MAPACHE_FACE_SEARCH_SESSION_TTL", default=3600)
MAPACHE_FACE_CONSENT_VERSION = env.str("MAPACHE_FACE_CONSENT_VERSION", default="1.0")
MAPACHE_FACE_SEARCH_RATE_LIMIT = env.int("MAPACHE_FACE_SEARCH_RATE_LIMIT", default=10)
MAPACHE_FACE_SEARCH_RATE_WINDOW = env.int("MAPACHE_FACE_SEARCH_RATE_WINDOW", default=600)
MAPACHE_BIB_ENGINE = env.str("MAPACHE_BIB_ENGINE", default="paddleocr")
MAPACHE_BIB_PADDLE_DET_MODEL = env.str(
    "MAPACHE_BIB_PADDLE_DET_MODEL", default="PP-OCRv6_small_det"
)
MAPACHE_BIB_PADDLE_REC_MODEL = env.str(
    "MAPACHE_BIB_PADDLE_REC_MODEL", default="PP-OCRv6_small_rec"
)
MAPACHE_BIB_PADDLE_DEVICE = env.str("MAPACHE_BIB_PADDLE_DEVICE", default="cpu")
MAPACHE_BIB_MIN_CONFIDENCE = env.float("MAPACHE_BIB_MIN_CONFIDENCE", default=0.60)
MAPACHE_BIB_SEARCH_LIMIT = env.int("MAPACHE_BIB_SEARCH_LIMIT", default=100)
MAPACHE_BIB_SEARCH_RATE_LIMIT = env.int("MAPACHE_BIB_SEARCH_RATE_LIMIT", default=30)
MAPACHE_BIB_SEARCH_RATE_WINDOW = env.int("MAPACHE_BIB_SEARCH_RATE_WINDOW", default=600)
MAPACHE_BIB_SEARCH_SESSION_TTL = env.int("MAPACHE_BIB_SEARCH_SESSION_TTL", default=3600)
MAPACHE_BIB_OCR_TIMEOUT = env.int("MAPACHE_BIB_OCR_TIMEOUT", default=20)
MAPACHE_COMBINED_FACE_WEIGHT = env.float("MAPACHE_COMBINED_FACE_WEIGHT", default=0.5)
MAPACHE_COMBINED_BIB_WEIGHT = env.float("MAPACHE_COMBINED_BIB_WEIGHT", default=0.5)
MAPACHE_COMBINED_RRF_K = env.int("MAPACHE_COMBINED_RRF_K", default=60)
MAPACHE_COMBINED_SEARCH_LIMIT = env.int("MAPACHE_COMBINED_SEARCH_LIMIT", default=100)
MAPACHE_COMBINED_SEARCH_RATE_LIMIT = env.int("MAPACHE_COMBINED_SEARCH_RATE_LIMIT", default=10)
MAPACHE_COMBINED_SEARCH_RATE_WINDOW = env.int("MAPACHE_COMBINED_SEARCH_RATE_WINDOW", default=600)
MAPACHE_COMBINED_SEARCH_SESSION_TTL = env.int("MAPACHE_COMBINED_SEARCH_SESSION_TTL", default=3600)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "{asctime} {levelname} {name}: {message}",
            "style": "{",
        }
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "standard"}},
    "root": {"handlers": ["console"], "level": env.str("DJANGO_LOG_LEVEL", "INFO")},
}
LOGGING["loggers"] = {
    "mapache.media_processing": {
        "handlers": ["console"],
        "level": env.str("DJANGO_LOG_LEVEL", "INFO"),
        "propagate": False,
    },
    "mapache.ai": {
        "handlers": ["console"],
        "level": env.str("DJANGO_LOG_LEVEL", "INFO"),
        "propagate": False,
    },
    "mapache.storage": {
        "handlers": ["console"],
        "level": env.str("DJANGO_LOG_LEVEL", "INFO"),
        "propagate": False,
    },
    "mapache.downloads": {
        "handlers": ["console"],
        "level": env.str("DJANGO_LOG_LEVEL", "INFO"),
        "propagate": False,
    },
    "mapache.uploads": {
        "handlers": ["console"],
        "level": env.str("DJANGO_LOG_LEVEL", "INFO"),
        "propagate": False,
    },
}
