import re

from django.core.exceptions import ValidationError

from apps.mapache_ai.models import GalleryAISettings

_SEPARATORS = re.compile(r"[\s._-]+")
_NUMERIC_CONFUSIONS = str.maketrans({"O": "0", "I": "1", "L": "1", "S": "5", "B": "8"})


def normalize_bib_text(
    raw_text: str, *, bib_format: str, min_length: int = 1, max_length: int = 16
) -> str | None:
    value = _SEPARATORS.sub("", str(raw_text).strip().upper())
    if not value:
        return None
    if bib_format == GalleryAISettings.BibFormat.NUMERIC:
        value = value.translate(_NUMERIC_CONFUSIONS)
        if not value.isascii() or not value.isdigit():
            return None
    elif bib_format == GalleryAISettings.BibFormat.ALPHANUMERIC:
        if not value.isascii() or not value.isalnum():
            return None
    else:
        raise ValidationError("Formato de dorsal desconocido.")
    if not min_length <= len(value) <= max_length:
        return None
    return value
