import pytest

from apps.galleries.models import Gallery
from apps.galleries.selectors import (
    get_event_galleries,
    get_featured_galleries,
    get_public_galleries,
)
from apps.galleries.services import publish_gallery
from tests.factories import make_gallery, make_user

pytestmark = pytest.mark.django_db


def make_published(user, title, **kwargs):
    gallery = make_gallery(user, title=title, **kwargs)
    return publish_gallery(gallery=gallery, published_by=user)


def test_public_selector_includes_only_portfolio_public_published():
    user = make_user()
    eligible = make_published(user, "Visible", show_in_portfolio=True)
    make_published(
        user,
        "No listada",
        visibility=Gallery.Visibility.UNLISTED,
        show_in_portfolio=True,
    )
    private = make_gallery(
        user,
        title="Privada",
        visibility=Gallery.Visibility.PRIVATE_PIN,
        show_in_portfolio=True,
    )
    private.set_pin("4821")
    private.save(update_fields=["pin_hash"])
    publish_gallery(gallery=private, published_by=user)
    make_gallery(user, title="Borrador", show_in_portfolio=True)
    archived = make_published(user, "Archivada", show_in_portfolio=True)
    archived.status = Gallery.Status.ARCHIVED
    archived.save(update_fields=["status"])
    make_published(user, "Oculta", show_in_portfolio=False)

    assert list(get_public_galleries()) == [eligible]


def test_featured_selector_requires_all_public_conditions():
    user = make_user()
    featured = make_published(
        user,
        "Destacada",
        show_in_portfolio=True,
        is_featured=True,
    )
    make_published(user, "Normal", show_in_portfolio=True, is_featured=False)
    make_published(user, "Oculta", show_in_portfolio=False, is_featured=True)

    assert list(get_featured_galleries()) == [featured]


def test_event_selector_includes_listed_public_and_private_events():
    user = make_user()
    public = make_published(user, "Público", show_in_portfolio=True)
    private = make_gallery(
        user,
        title="Privado",
        visibility=Gallery.Visibility.PRIVATE_PIN,
        show_in_portfolio=True,
    )
    private.set_pin("4821")
    private.save(update_fields=["pin_hash"])
    private = publish_gallery(gallery=private, published_by=user)
    make_published(user, "Oculto", show_in_portfolio=False)
    make_published(
        user,
        "No listado",
        visibility=Gallery.Visibility.UNLISTED,
        show_in_portfolio=True,
    )

    assert list(get_event_galleries()) == [public, private]
