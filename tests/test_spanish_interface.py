import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.models import User
from apps.galleries.direct_uploads.services import initialize_uploads
from apps.galleries.forms import GalleryAccessForm, GalleryCreateForm, GalleryEditForm
from apps.galleries.models import Gallery, GalleryUploadItem, Photo
from apps.mapache_ai.models import FaceEmbedding, GalleryAISettings
from tests.factories import make_gallery, make_user

pytestmark = pytest.mark.django_db


def test_gallery_forms_use_spanish_labels():
    user = make_user()
    gallery = make_gallery(user)

    assert {name: field.label for name, field in GalleryCreateForm().fields.items()} == {
        "title": "Nombre de la galería",
        "event_date": "Fecha del evento",
        "description": "Descripción",
        "visibility": "Visibilidad",
        "allow_photo_download": "Permitir descarga individual",
        "allow_gallery_download": "Permitir descarga completa",
        "show_in_portfolio": "Mostrar en portafolio",
        "is_featured": "Destacar en inicio",
        "pin": None,
        "enable_mapache_ai": "Activar Mapache AI en este evento",
    }
    assert GalleryEditForm().fields["title"].label == "Nombre de la galería"
    access = GalleryAccessForm(instance=gallery)
    assert access.fields["visibility"].label == "Visibilidad"
    assert access.fields["allow_photo_download"].label == "Permitir descarga individual"
    assert access.fields["allow_gallery_download"].label == "Permitir descarga completa"


def test_dashboard_pages_do_not_show_remaining_english_terms(client, settings):
    user = make_user()
    gallery = make_gallery(user, visibility=Gallery.Visibility.UNLISTED)
    client.force_login(user)

    gallery_list = client.get(reverse("galleries_dashboard:list")).content.decode()
    assert "OCULTA" in gallery_list
    assert ">UNLISTED<" not in gallery_list

    settings.MAPACHE_AI_ENABLED = False
    ai_page = client.get(
        reverse("mapache_ai_dashboard:settings", args=[gallery.uuid])
    ).content.decode()
    assert "Ordena por relevancia" in ai_page
    assert "representaciones biométricas" in ai_page
    assert "rankeada" not in ai_page
    assert "embeddings" not in ai_page
    assert "pgvector" not in ai_page


def test_administrative_labels_and_staff_role_are_in_spanish():
    assert User.Role.STAFF.label == "Personal"
    assert str(User._meta.get_field("role").verbose_name) == "rol"
    assert str(Gallery._meta.get_field("title").verbose_name) == "título"
    assert str(Photo._meta.get_field("processing_status").verbose_name) == (
        "estado de procesamiento"
    )
    assert str(GalleryUploadItem._meta.get_field("upload_mode").verbose_name) == ("modo de carga")
    assert GalleryUploadItem.UploadMode.MULTIPART.label == "Carga por partes"
    assert str(GalleryAISettings._meta.get_field("enabled").verbose_name) == "activado"
    assert FaceEmbedding._meta.verbose_name == "representación facial"


def test_direct_upload_validation_messages_are_in_spanish(settings):
    settings.MAPACHE_DIRECT_UPLOAD_ENABLED = True
    settings.STORAGE_BACKEND = "r2"
    user = make_user()
    gallery = make_gallery(user)

    with pytest.raises(ValidationError, match="Tipo de contenido no permitido"):
        initialize_uploads(
            gallery=gallery,
            user=user,
            metadata=[
                {
                    "name": "foto.jpg",
                    "size": 100,
                    "type": "application/octet-stream",
                }
            ],
            client=object(),
        )
