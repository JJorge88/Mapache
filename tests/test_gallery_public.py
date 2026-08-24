import pytest
from django.urls import reverse

from apps.galleries.models import Gallery, Photo
from apps.galleries.services import (
    archive_gallery,
    change_gallery_pin,
    publish_gallery,
)
from apps.mapache_ai.models import GalleryAISettings
from tests.factories import make_gallery, make_photo, make_user

pytestmark = pytest.mark.django_db


def test_published_public_gallery_returns_200(client):
    user = make_user()
    gallery = publish_gallery(gallery=make_gallery(user), published_by=user)

    response = client.get(reverse("galleries_public:detail", args=[gallery.slug]))

    assert response.status_code == 200
    assert gallery.title in response.content.decode()
    assert "MAPACHE / GALERÍA" in response.content.decode()
    assert "data-gallery-lightbox" in response.content.decode()
    assert "gallery-mobile-nav" in response.content.decode()
    assert "Tus recuerdos." in response.content.decode()


def test_public_gallery_preserves_photo_orientation_and_dimensions(client):
    user = make_user()
    gallery = publish_gallery(gallery=make_gallery(user), published_by=user)
    portrait = make_photo(gallery, user, "vertical.jpg")
    portrait.processing_status = Photo.ProcessingStatus.READY
    portrait.optimized_file.name = f"optimized/{portrait.uuid}.webp"
    portrait.orientation = Photo.Orientation.PORTRAIT
    portrait.width = 800
    portrait.height = 1200
    portrait.save(
        update_fields=[
            "processing_status",
            "optimized_file",
            "orientation",
            "width",
            "height",
        ]
    )

    page = client.get(reverse("galleries_public:detail", args=[gallery.slug])).content.decode()

    assert "public-photo-portrait" in page
    assert 'width="800" height="1200"' in page
    assert "1 FOTOGRAFÍA" in page
    assert "gallery-masonry" in page


@pytest.mark.parametrize("state", [Gallery.Status.DRAFT, Gallery.Status.ARCHIVED])
def test_non_published_gallery_returns_404(client, state):
    user = make_user()
    gallery = make_gallery(user)
    if state == Gallery.Status.ARCHIVED:
        gallery = archive_gallery(gallery=gallery, archived_by=user)

    response = client.get(reverse("galleries_public:detail", args=[gallery.slug]))

    assert response.status_code == 404


def test_private_gallery_redirects_without_session_access(client):
    user = make_user()
    gallery = make_gallery(user, visibility=Gallery.Visibility.PRIVATE_PIN)
    change_gallery_pin(gallery=gallery, pin="0421", changed_by=user)
    gallery = publish_gallery(gallery=gallery, published_by=user)

    response = client.get(reverse("galleries_public:detail", args=[gallery.slug]))

    assert response.status_code == 302
    assert response.url == reverse("galleries_public:access", args=[gallery.slug])


def test_wrong_pin_does_not_grant_access(client):
    user = make_user()
    gallery = make_gallery(user, visibility=Gallery.Visibility.PRIVATE_PIN)
    change_gallery_pin(gallery=gallery, pin="0421", changed_by=user)
    gallery = publish_gallery(gallery=gallery, published_by=user)

    response = client.post(
        reverse("galleries_public:access", args=[gallery.slug]),
        {"pin": "9999"},
    )

    assert response.status_code == 200
    assert not client.session.get(f"gallery_access_{gallery.uuid}")


def test_correct_pin_grants_access_without_storing_pin(client):
    user = make_user()
    gallery = make_gallery(user, visibility=Gallery.Visibility.PRIVATE_PIN)
    change_gallery_pin(gallery=gallery, pin="0421", changed_by=user)
    gallery = publish_gallery(gallery=gallery, published_by=user)

    response = client.post(
        reverse("galleries_public:access", args=[gallery.slug]),
        {"pin": "0421"},
    )

    assert response.status_code == 302
    assert client.session[f"gallery_access_{gallery.uuid}"] is True
    assert "0421" not in str(dict(client.session))
    assert client.get(reverse("galleries_public:detail", args=[gallery.slug])).status_code == 200


def test_unlisted_gallery_is_directly_accessible_but_absent_from_portfolio(client):
    user = make_user()
    gallery = publish_gallery(
        gallery=make_gallery(
            user,
            visibility=Gallery.Visibility.UNLISTED,
            show_in_portfolio=True,
        ),
        published_by=user,
    )

    detail = client.get(reverse("galleries_public:detail", args=[gallery.slug]))
    portfolio = client.get(reverse("galleries_public:portfolio"))

    assert detail.status_code == 200
    assert gallery.title not in portfolio.content.decode()


def test_events_page_lists_private_gallery_with_lock_without_exposing_cover(client):
    user = make_user()
    gallery = make_gallery(
        user,
        title="Evento protegido",
        visibility=Gallery.Visibility.PRIVATE_PIN,
        show_in_portfolio=True,
    )
    change_gallery_pin(gallery=gallery, pin="4821", changed_by=user)
    gallery = publish_gallery(gallery=gallery, published_by=user)
    cover = make_photo(gallery, user, "secreta.jpg")
    cover.processing_status = Photo.ProcessingStatus.READY
    cover.thumbnail_file.name = f"thumbnails/{cover.uuid}.webp"
    cover.optimized_file.name = f"optimized/{cover.uuid}.webp"
    cover.save(update_fields=["processing_status", "thumbnail_file", "optimized_file"])
    gallery.cover_photo = cover
    gallery.save(update_fields=["cover_photo"])

    page = client.get(reverse("galleries_public:portfolio")).content.decode()

    assert "Evento protegido" in page
    assert "EVENTO PRIVADO" in page
    assert "event-lock" in page
    assert str(cover.uuid) not in page


def test_events_search_filters_by_title(client):
    user = make_user()
    first = publish_gallery(
        gallery=make_gallery(user, title="Carrera nocturna", show_in_portfolio=True),
        published_by=user,
    )
    second = publish_gallery(
        gallery=make_gallery(user, title="Rodeo", show_in_portfolio=True),
        published_by=user,
    )

    page = client.get(reverse("galleries_public:portfolio"), {"q": "carrera"}).content.decode()

    assert first.title in page
    assert second.title not in page


def test_event_card_reports_mapache_ai_when_available(client):
    user = make_user()
    gallery = publish_gallery(
        gallery=make_gallery(user, title="Carrera AI", show_in_portfolio=True),
        published_by=user,
    )
    GalleryAISettings.objects.create(
        gallery=gallery,
        enabled=True,
        face_search_enabled=True,
    )

    page = client.get(reverse("galleries_public:portfolio")).content.decode()

    assert "Carrera AI" in page
    assert "✦ MAPACHE AI" in page


def test_private_event_card_never_reuses_previous_public_cover(client):
    user = make_user()
    public = publish_gallery(
        gallery=make_gallery(user, title="Público", show_in_portfolio=True),
        published_by=user,
    )
    public_cover = make_photo(public, user, "publica.jpg")
    public_cover.processing_status = Photo.ProcessingStatus.READY
    public_cover.thumbnail_file.name = f"thumbnails/{public_cover.uuid}.webp"
    public_cover.save(update_fields=["processing_status", "thumbnail_file"])
    public.cover_photo = public_cover
    public.save(update_fields=["cover_photo"])
    private = make_gallery(
        user,
        title="Privado",
        visibility=Gallery.Visibility.PRIVATE_PIN,
        show_in_portfolio=True,
    )
    change_gallery_pin(gallery=private, pin="4821", changed_by=user)
    publish_gallery(gallery=private, published_by=user)

    page = client.get(reverse("galleries_public:portfolio")).content.decode()
    private_card = page.split('class="event-card is-private"', 1)[1].split("</article>", 1)[0]

    assert "event-private-art" in private_card
    assert str(public_cover.uuid) not in private_card


def test_archived_gallery_cannot_be_bypassed_with_old_session_access(client):
    user = make_user()
    gallery = make_gallery(user, visibility=Gallery.Visibility.PRIVATE_PIN)
    change_gallery_pin(gallery=gallery, pin="4821", changed_by=user)
    gallery = publish_gallery(gallery=gallery, published_by=user)
    session = client.session
    session[f"gallery_access_{gallery.uuid}"] = True
    session.save()
    gallery = archive_gallery(gallery=gallery, archived_by=user)

    assert client.get(reverse("galleries_public:detail", args=[gallery.slug])).status_code == 404


def test_pin_attempts_are_temporarily_limited(client):
    user = make_user()
    gallery = make_gallery(user, visibility=Gallery.Visibility.PRIVATE_PIN)
    change_gallery_pin(gallery=gallery, pin="4821", changed_by=user)
    gallery = publish_gallery(gallery=gallery, published_by=user)
    url = reverse("galleries_public:access", args=[gallery.slug])

    for _ in range(5):
        client.post(url, {"pin": "9999"})
    blocked = client.post(url, {"pin": "4821"})

    assert blocked.status_code == 200
    assert not client.session.get(f"gallery_access_{gallery.uuid}")
    assert "Demasiados intentos" in blocked.content.decode()


def test_pin_hash_is_not_exposed_in_public_or_dashboard_templates(client):
    user = make_user()
    gallery = make_gallery(user, visibility=Gallery.Visibility.PRIVATE_PIN)
    change_gallery_pin(gallery=gallery, pin="4821", changed_by=user)
    gallery = publish_gallery(gallery=gallery, published_by=user)

    public_response = client.get(reverse("galleries_public:access", args=[gallery.slug]))
    client.force_login(user)
    dashboard_response = client.get(reverse("galleries_dashboard:access", args=[gallery.uuid]))

    assert gallery.pin_hash not in public_response.content.decode()
    assert gallery.pin_hash not in dashboard_response.content.decode()
    assert "4821" not in dashboard_response.content.decode()
