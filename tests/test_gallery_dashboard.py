import pytest
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.galleries.models import Gallery
from apps.galleries.services import publish_gallery
from apps.mapache_ai.models import GalleryAISettings
from tests.factories import make_gallery, make_user

pytestmark = pytest.mark.django_db


def test_anonymous_user_cannot_access_gallery_dashboard_pages(client):
    user = make_user()
    gallery = make_gallery(user)
    urls = [
        reverse("galleries_dashboard:list"),
        reverse("galleries_dashboard:create"),
        reverse("galleries_dashboard:detail", args=[gallery.uuid]),
        reverse("galleries_dashboard:edit", args=[gallery.uuid]),
        reverse("galleries_dashboard:access", args=[gallery.uuid]),
    ]

    for url in urls:
        response = client.get(url)
        assert response.status_code == 302
        assert reverse("accounts:login") in response.url


def test_authenticated_user_can_list_and_search_galleries(client):
    user = make_user()
    gallery = make_gallery(user, title="Rodeo Chiquimula")
    make_gallery(user, title="Boda Antigua")
    client.force_login(user)

    response = client.get(reverse("galleries_dashboard:list"), {"q": "Rodeo"})

    content = response.content.decode()
    assert response.status_code == 200
    assert gallery.title in content
    assert "Boda Antigua" not in content


def test_dashboard_gallery_create_flow(client):
    user = make_user()
    client.force_login(user)

    response = client.post(
        reverse("galleries_dashboard:create"),
        {
            "title": "Rodeo Zacapa",
            "event_date": "2026-08-22",
            "description": "Una tarde de competencia.",
            "visibility": Gallery.Visibility.PRIVATE_PIN,
            "pin": "0421",
            "allow_photo_download": "on",
            "show_in_portfolio": "on",
            "enable_mapache_ai": "on",
        },
    )

    gallery = Gallery.objects.get(title="Rodeo Zacapa")
    assert response.status_code == 302
    assert response.url == reverse("galleries_dashboard:detail", args=[gallery.uuid])
    assert gallery.check_pin("0421")
    assert gallery.allow_photo_download is True
    ai_settings = GalleryAISettings.objects.get(gallery=gallery)
    assert ai_settings.enabled is True
    assert ai_settings.face_search_enabled is True
    assert ai_settings.bib_search_enabled is True


def test_dashboard_gallery_edit_keeps_protected_fields(client):
    user = make_user()
    gallery = make_gallery(user, title="Nombre inicial")
    original_slug = gallery.slug
    client.force_login(user)

    response = client.post(
        reverse("galleries_dashboard:edit", args=[gallery.uuid]),
        {
            "title": "Nombre editado",
            "event_date": "",
            "description": "Actualizada",
            "allow_gallery_download": "on",
        },
    )
    gallery.refresh_from_db()

    assert response.status_code == 302
    assert gallery.title == "Nombre editado"
    assert gallery.slug == original_slug
    assert gallery.status == Gallery.Status.DRAFT
    assert gallery.allow_gallery_download is True


def test_dashboard_publish_and_archive_actions(client):
    user = make_user()
    gallery = make_gallery(user)
    client.force_login(user)

    publish_response = client.post(reverse("galleries_dashboard:publish", args=[gallery.uuid]))
    gallery.refresh_from_db()
    assert publish_response.status_code == 302
    assert gallery.status == Gallery.Status.PUBLISHED

    archive_response = client.post(reverse("galleries_dashboard:archive", args=[gallery.uuid]))
    gallery.refresh_from_db()
    assert archive_response.status_code == 302
    assert gallery.status == Gallery.Status.ARCHIVED


def test_dashboard_access_settings_change_visibility_pin_and_downloads(client):
    user = make_user()
    gallery = publish_gallery(gallery=make_gallery(user), published_by=user)
    client.force_login(user)

    response = client.post(
        reverse("galleries_dashboard:access", args=[gallery.uuid]),
        {
            "visibility": Gallery.Visibility.PRIVATE_PIN,
            "pin": "928174",
            "allow_photo_download": "on",
            "allow_gallery_download": "on",
        },
    )
    gallery.refresh_from_db()

    assert response.status_code == 302
    assert gallery.visibility == Gallery.Visibility.PRIVATE_PIN
    assert gallery.check_pin("928174")
    assert gallery.allow_photo_download is True
    assert gallery.allow_gallery_download is True
    assert "928174" not in str(list(AuditLog.objects.values_list("metadata", flat=True)))


def test_dashboard_home_shows_real_counts(client):
    user = make_user()
    make_gallery(user)
    publish_gallery(gallery=make_gallery(user, title="Publicada"), published_by=user)
    client.force_login(user)

    response = client.get(reverse("accounts:dashboard"))

    assert response.status_code == 200
    assert response.context["total_galleries"] == 2
    assert response.context["published_galleries"] == 1
