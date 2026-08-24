from django.core.paginator import Page, Paginator
from django.db.models import Case, Count, Exists, IntegerField, OuterRef, Q, QuerySet, Value, When

from .models import Gallery, Photo


def get_public_galleries() -> QuerySet[Gallery]:
    return (
        Gallery.objects.filter(
            status=Gallery.Status.PUBLISHED,
            visibility=Gallery.Visibility.PUBLIC,
            show_in_portfolio=True,
        )
        .select_related("cover_photo")
        .annotate(photo_count=Count("photos"))
    )


def get_featured_galleries() -> QuerySet[Gallery]:
    return get_public_galleries().filter(is_featured=True)


def get_event_galleries() -> QuerySet[Gallery]:
    """Published, intentionally listed events without exposing unlisted galleries."""
    from apps.mapache_ai.models import GalleryAISettings

    ai_available = GalleryAISettings.objects.filter(
        gallery_id=OuterRef("pk"),
        enabled=True,
    ).filter(Q(face_search_enabled=True) | Q(bib_search_enabled=True))
    return (
        Gallery.objects.filter(
            status=Gallery.Status.PUBLISHED,
            visibility__in=[Gallery.Visibility.PUBLIC, Gallery.Visibility.PRIVATE_PIN],
            show_in_portfolio=True,
        )
        .select_related("cover_photo")
        .annotate(
            photo_count=Count("photos"),
            ai_available=Exists(ai_available),
            private_sort=Case(
                When(visibility=Gallery.Visibility.PRIVATE_PIN, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )
        .order_by("private_sort", "-event_date", "-created_at")
    )


def get_gallery_by_slug(slug: str) -> Gallery:
    return Gallery.objects.select_related("cover_photo").get(
        slug=slug,
        status=Gallery.Status.PUBLISHED,
    )


def get_gallery_photos(gallery: Gallery) -> QuerySet[Photo]:
    return gallery.photos.select_related("uploaded_by").all()


def get_public_gallery_photos(gallery: Gallery) -> QuerySet[Photo]:
    return gallery.photos.filter(
        processing_status=Photo.ProcessingStatus.READY,
        optimized_file__gt="",
    )


def get_gallery_processing_stats(gallery: Gallery) -> dict[str, int]:
    counts = {
        item["processing_status"]: item["count"]
        for item in gallery.photos.values("processing_status").annotate(count=Count("id"))
    }
    return {
        "total": sum(counts.values()),
        "pending": counts.get(Photo.ProcessingStatus.PENDING, 0),
        "processing": counts.get(Photo.ProcessingStatus.PROCESSING, 0),
        "ready": counts.get(Photo.ProcessingStatus.READY, 0),
        "error": counts.get(Photo.ProcessingStatus.ERROR, 0),
    }


def get_dashboard_galleries() -> QuerySet[Gallery]:
    return Gallery.objects.select_related("cover_photo", "created_by").annotate(
        photo_count=Count("photos")
    )


def get_recent_galleries(*, limit: int = 6) -> QuerySet[Gallery]:
    return get_dashboard_galleries().order_by("-created_at")[:limit]


def get_gallery_photo_page(
    gallery: Gallery,
    *,
    page_number: int | str = 1,
    per_page: int = 60,
) -> Page:
    queryset = gallery.photos.select_related("uploaded_by").order_by("sort_order", "created_at")
    return Paginator(queryset, per_page).get_page(page_number)


def get_gallery_photo_order(gallery: Gallery) -> list[str]:
    return [
        str(value)
        for value in gallery.photos.order_by("sort_order", "created_at").values_list(
            "uuid", flat=True
        )
    ]
