import re

import pytest
from django.urls import reverse

from tests.factories import make_gallery, make_user

pytestmark = pytest.mark.django_db


def test_mapache_ai_sidebar_link_reaches_existing_dashboard(client):
    user = make_user()
    gallery = make_gallery(user)
    client.force_login(user)

    response = client.get(reverse("mapache_ai_entry"))

    assert response.status_code == 302
    assert response.url == reverse("mapache_ai_dashboard:settings", args=[gallery.uuid])


def test_mapache_ai_sidebar_entry_without_galleries_returns_to_gallery_list(client):
    user = make_user()
    client.force_login(user)

    response = client.get(reverse("mapache_ai_entry"))

    assert response.status_code == 302
    assert response.url == reverse("galleries_dashboard:list")


def test_sidebar_removes_only_mapache_ai_coming_soon_label(client):
    user = make_user()
    make_gallery(user)
    client.force_login(user)

    html = client.get(reverse("accounts:dashboard")).content.decode()

    assert f'href="{reverse("mapache_ai_entry")}"' in html
    assert "Mapache AI <small>Próximamente</small>" not in html
    assert (
        '<span class="nav-item future" aria-disabled="true">'
        '<span class="nav-index">04</span> Mensajes <small>Próximamente</small></span>' in html
    )


def test_mapache_ai_sidebar_item_is_active_inside_module(client):
    user = make_user()
    gallery = make_gallery(user)
    client.force_login(user)

    html = client.get(
        reverse("mapache_ai_dashboard:settings", args=[gallery.uuid])
    ).content.decode()
    link = re.search(
        rf'<a class="([^"]*)" href="{re.escape(reverse("mapache_ai_entry"))}"'
        r' aria-current="page"><span class="nav-index">03</span> Mapache AI</a>',
        html,
    )

    assert link is not None
    assert "active" in link.group(1).split()


def test_mapache_ai_sidebar_entry_requires_authentication(client):
    response = client.get(reverse("mapache_ai_entry"))

    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))
