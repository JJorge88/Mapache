from django.contrib import messages
from django.core.cache import cache
from django.shortcuts import redirect, render

from apps.galleries.models import Gallery, Photo
from apps.galleries.selectors import get_featured_galleries, get_public_galleries

from .forms import ContactInquiryForm


def _visitor_key(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",", 1)[0].strip()
    address = forwarded or request.META.get("REMOTE_ADDR", "unknown")
    return f"mapache:public-contact:{address}"


def home(request):
    covers = {
        "cover_photo__processing_status": Photo.ProcessingStatus.READY,
        "cover_photo__optimized_file__gt": "",
    }
    featured_galleries = list(get_featured_galleries().filter(**covers)[:3])
    latest_galleries = list(featured_galleries)
    if len(latest_galleries) < 3:
        used_ids = [gallery.pk for gallery in latest_galleries]
        latest_galleries.extend(
            get_public_galleries()
            .filter(**covers)
            .exclude(pk__in=used_ids)
            .order_by("-event_date", "-created_at")[: 3 - len(latest_galleries)]
        )
    ai_gallery = (
        Gallery.objects.filter(
            status=Gallery.Status.PUBLISHED,
            visibility=Gallery.Visibility.PUBLIC,
            ai_settings__enabled=True,
        )
        .order_by("-event_date", "-created_at")
        .first()
    )
    hero_gallery = featured_galleries[0] if featured_galleries else None
    if hero_gallery is None and latest_galleries:
        hero_gallery = latest_galleries[0]
    return render(
        request,
        "website/home.html",
        {
            "featured_galleries": featured_galleries,
            "latest_galleries": latest_galleries,
            "hero_gallery": hero_gallery,
            "ai_gallery": ai_gallery,
        },
    )


def services(request):
    return render(request, "website/services.html")


def studio(request):
    return render(request, "website/studio.html")


def contact(request):
    if request.method == "POST":
        key = _visitor_key(request)
        attempts = int(cache.get(key, 0))
        form = ContactInquiryForm(request.POST)
        if attempts >= 5:
            form.add_error(None, "Has enviado varios mensajes. Intenta de nuevo más tarde.")
        elif form.is_valid():
            form.save()
            cache.set(key, attempts + 1, timeout=3600)
            messages.success(
                request,
                "Recibimos tu proyecto. Te responderemos lo antes posible.",
            )
            return redirect("website:contact")
    else:
        form = ContactInquiryForm()
    return render(request, "website/contact.html", {"form": form})
