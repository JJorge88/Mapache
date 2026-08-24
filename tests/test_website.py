import pytest
from django.urls import reverse

from apps.galleries.models import Photo
from apps.galleries.services import publish_gallery
from apps.website.models import ContactInquiry
from tests.factories import make_gallery, make_photo, make_user

pytestmark = pytest.mark.django_db


def test_homepage_returns_200(client):
    response = client.get(reverse("website:home"))

    assert response.status_code == 200
    page = response.content.decode()
    assert "Capturamos la" in page
    assert "producimos la" in page
    assert "LO QUE HACEMOS" in page
    assert "Últimas Entregas" in page
    assert "MAPACHE AI" in page
    assert "La experiencia no termina" in page
    assert "EVENTOS" in page
    assert "data-hero-slide" in page


@pytest.mark.parametrize(
    ("route_name", "heading"),
    [
        ("website:services", "Soluciones Audiovisuales"),
        ("website:studio", "Detrás de cada imagen"),
        ("website:contact", "Hagamos que tu"),
    ],
)
def test_public_editorial_pages_return_200(client, route_name, heading):
    response = client.get(reverse(route_name))

    assert response.status_code == 200
    assert heading in response.content.decode()
    assert "EVENTOS" in response.content.decode()


def test_contact_form_is_available_and_stores_inquiry(client):
    page = client.get(reverse("website:contact")).content.decode()

    assert 'id="id_name"' in page
    assert "Enviar proyecto" in page

    response = client.post(
        reverse("website:contact"),
        {
            "name": "Ana López",
            "email": "ana@example.com",
            "phone": "+502 5555 5555",
            "service": "PLATFORM",
            "message": "Necesitamos una plataforma para nuestro evento deportivo.",
            "website": "",
        },
    )

    assert response.status_code == 302
    inquiry = ContactInquiry.objects.get()
    assert inquiry.name == "Ana López"
    assert inquiry.service == ContactInquiry.Service.PLATFORM


def test_contact_honeypot_rejects_spam(client):
    response = client.post(
        reverse("website:contact"),
        {
            "name": "Robot",
            "email": "robot@example.com",
            "service": "OTHER",
            "message": "Mensaje automático.",
            "website": "https://spam.example",
        },
    )

    assert response.status_code == 200
    assert not ContactInquiry.objects.exists()


def test_home_uses_only_ready_public_featured_cover(client):
    user = make_user()
    featured = make_gallery(
        user,
        title="Historia pública",
        show_in_portfolio=True,
        is_featured=True,
    )
    cover = make_photo(featured, user, "publica.jpg")
    cover.processing_status = Photo.ProcessingStatus.READY
    cover.optimized_file.name = f"optimized/{cover.uuid}.webp"
    cover.thumbnail_file.name = f"thumbnails/{cover.uuid}.webp"
    cover.width = 1200
    cover.height = 800
    cover.save(
        update_fields=[
            "processing_status",
            "optimized_file",
            "thumbnail_file",
            "width",
            "height",
        ]
    )
    featured.cover_photo = cover
    featured.save(update_fields=["cover_photo"])
    publish_gallery(gallery=featured, published_by=user)
    private = make_gallery(user, title="Historia privada", visibility="PRIVATE_PIN")

    response = client.get(reverse("website:home"))
    page = response.content.decode()

    assert featured.title in page
    assert private.title not in page
    assert response.context["hero_gallery"] == featured
    assert 'class="master-hero-slide is-active"' in page
