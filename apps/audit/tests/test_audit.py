import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import ProtectedError

from apps.accounts.models import User
from apps.audit import services
from apps.audit.admin import AuditEventAdmin
from apps.audit.models import AppendOnlyError, AuditEvent

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_user():
    return User.objects.create_superuser("+256700000001", password="a-long-admin-password")


@pytest.fixture
def target():
    return User.objects.create_user("+256772123456")


@pytest.fixture
def event(admin_user, target):
    return services.record(
        actor=admin_user,
        action="landlord.approved",
        target=target,
        evidence={"document_sha256": "ab" * 32, "reason": "ID matches"},
    )


def test_record_captures_target_and_actor(event, admin_user, target):
    assert event.actor == admin_user
    assert event.target_type == "accounts.user"
    assert event.target_id == str(target.pk)
    assert str(event) == f"landlord.approved on accounts.user:{target.pk}"


def test_hash_verifies_after_reload(event):
    assert services.verify(AuditEvent.objects.get(pk=event.pk))


def test_hash_detects_tampering(event):
    event.evidence = {"reason": "edited"}
    assert not services.verify(event)


def test_system_events_have_no_actor(target):
    event = services.record(actor=None, action="listing.flagged", target=target)
    assert event.actor is None and services.verify(event)


def test_instance_update_is_blocked(event):
    event.action = "landlord.rejected"
    with pytest.raises(AppendOnlyError):
        event.save()


def test_instance_delete_is_blocked(event):
    with pytest.raises(AppendOnlyError):
        event.delete()


def test_queryset_update_and_delete_are_blocked(event):
    with pytest.raises(AppendOnlyError):
        AuditEvent.objects.filter(pk=event.pk).update(action="x")
    with pytest.raises(AppendOnlyError):
        AuditEvent.objects.all().delete()


def test_actor_with_history_cannot_be_deleted(event, admin_user):
    with pytest.raises(ProtectedError):
        admin_user.delete()


@pytest.mark.skipif(connection.vendor != "postgresql", reason="trigger is PostgreSQL-only")
def test_database_trigger_blocks_raw_sql_update(event):
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("UPDATE audit_auditevent SET action = 'x' WHERE id = %s", [event.pk])


def test_admin_is_read_only(rf):
    from django.contrib.admin.sites import site

    model_admin = AuditEventAdmin(AuditEvent, site)
    request = rf.get("/")
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)
