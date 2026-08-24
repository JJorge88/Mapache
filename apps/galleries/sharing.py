from io import BytesIO
from urllib.parse import urlparse

import qrcode
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

from .models import Gallery


def get_public_gallery_url(gallery: Gallery) -> str:
    base_url = settings.PUBLIC_SITE_URL.rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ImproperlyConfigured("PUBLIC_SITE_URL debe ser una URL absoluta HTTP(S).")
    path = reverse("galleries_public:detail", args=[gallery.slug])
    return f"{base_url}{path}"


def generate_gallery_qr(gallery: Gallery) -> tuple[bytes, str]:
    public_url = get_public_gallery_url(gallery)
    qr = qrcode.QRCode(version=None, box_size=8, border=4)
    qr.add_data(public_url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#080808", back_color="#F5F5F3")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue(), public_url
