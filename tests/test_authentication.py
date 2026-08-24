import pytest
from django.urls import reverse

from apps.accounts.models import User


@pytest.mark.django_db
def test_dashboard_requires_login(client):
    response = client.get(reverse("accounts:dashboard"))

    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


@pytest.mark.django_db
def test_valid_login_allows_dashboard_access(client):
    User.objects.create_user(username="mapache", password="segura-123")

    response = client.post(
        reverse("accounts:login"),
        {"username": "mapache", "password": "segura-123"},
    )

    assert response.status_code == 302
    assert response.url == reverse("accounts:dashboard")
    assert client.get(reverse("accounts:dashboard")).status_code == 200


@pytest.mark.django_db
def test_invalid_login_does_not_authenticate(client):
    User.objects.create_user(username="mapache", password="segura-123")

    response = client.post(
        reverse("accounts:login"),
        {"username": "mapache", "password": "incorrecta"},
    )

    assert response.status_code == 200
    assert "_auth_user_id" not in client.session
    assert response.context["form"].errors


@pytest.mark.django_db
def test_logout_ends_session_and_only_accepts_post(client):
    user = User.objects.create_user(username="mapache", password="segura-123")
    client.force_login(user)

    get_response = client.get(reverse("accounts:logout"))
    response = client.post(reverse("accounts:logout"))

    assert get_response.status_code == 405
    assert response.status_code == 302
    assert response.url == reverse("accounts:login")
    assert "_auth_user_id" not in client.session
