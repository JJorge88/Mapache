from django import template
from django.core.exceptions import PermissionDenied

from apps.core.media_delivery import get_photo_delivery_url

register = template.Library()


@register.simple_tag(takes_context=True)
def photo_delivery_url(context, photo, variant, audience="public"):
    try:
        return get_photo_delivery_url(
            photo=photo,
            variant=variant,
            request=context.get("request"),
            audience=audience,
        )
    except (PermissionDenied, ValueError):
        return ""
