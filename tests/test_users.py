import pytest

from apps.accounts.models import User


@pytest.mark.django_db
def test_user_can_be_created():
    user = User.objects.create_user(username="fotografo", password="segura-123")

    assert user.check_password("segura-123")
    assert user.role == User.Role.STAFF


@pytest.mark.django_db
def test_roles_are_available():
    admin = User.objects.create_user(username="admin-role", role=User.Role.ADMIN)

    assert admin.role == "ADMIN"
    assert set(User.Role.values) == {"ADMIN", "STAFF"}


@pytest.mark.django_db
def test_superuser_has_full_access_and_admin_role():
    user = User.objects.create_superuser(username="root", password="segura-123")

    assert user.is_superuser is True
    assert user.is_staff is True
    assert user.role == User.Role.ADMIN
    assert user.has_perm("any.permission") is True
