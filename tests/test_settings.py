from django.conf import settings


def test_django_settings_load_with_postgresql():
    assert settings.AUTH_USER_MODEL == "accounts.User"
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"
    assert settings.TIME_ZONE == "America/Guatemala"
    assert settings.USE_TZ is True
