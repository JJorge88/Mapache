import pytest

from apps.accounts.models import User
from apps.audit.models import AuditLog


@pytest.mark.django_db
def test_audit_log_can_be_created():
    user = User.objects.create_user(username="auditor")

    log = AuditLog.objects.create(
        user=user,
        action="created",
        model_name="Example",
        object_id="42",
        metadata={"source": "test"},
    )

    assert log.pk is not None
    assert log.metadata == {"source": "test"}
    assert log.user == user


@pytest.mark.django_db
def test_audit_log_accepts_system_events():
    log = AuditLog.objects.create(action="sync", model_name="System")

    assert log.user is None
    assert log.metadata == {}
