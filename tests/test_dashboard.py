import pytest
from django.urls import reverse

from apps.accounts.models import User


@pytest.mark.django_db
def test_authenticated_dashboard_returns_200(client):
    user = User.objects.create_user(username="staff")
    client.force_login(user)

    response = client.get(reverse("accounts:dashboard"))

    assert response.status_code == 200
    assert "Todo listo para crear y entregar" in response.content.decode()
