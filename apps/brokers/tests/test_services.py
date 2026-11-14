import pytest
from django.core.exceptions import PermissionDenied

from apps.brokers import services
from apps.brokers.models import BrokerProfile
from apps.core.roles import Role
from conftest import in_hours

pytestmark = pytest.mark.django_db


def _onboard(actor, phone="0755222333", areas=("Ntinda",)):
    return services.onboard_broker(
        actor=actor,
        phone=phone,
        full_name="Grace",
        id_checked_at=in_hours(-2),
        terms_signed_at=in_hours(-1),
        areas=list(areas),
    )


def test_onboarding_creates_phone_account(coordinator):
    broker = _onboard(coordinator)
    assert broker.user.phone == "+256755222333"
    assert not broker.user.has_usable_password()  # signs in by SMS code
    assert services.active_broker_for(broker.user) == broker
    with pytest.raises(services.BrokerError, match="already"):
        _onboard(coordinator)


def test_onboarding_rules(coordinator, make_staff, customer):
    with pytest.raises(PermissionDenied):
        _onboard(make_staff(Role.FINANCE))
    with pytest.raises(services.BrokerError, match="service area"):
        _onboard(coordinator, areas=["Entebbe"])
    staff = make_staff(Role.FINANCE)
    with pytest.raises(services.BrokerError, match="Staff"):
        _onboard(coordinator, phone=staff.phone)
    customer.is_active = False
    customer.save()
    with pytest.raises(services.BrokerError, match="suspended"):
        _onboard(coordinator, phone=customer.phone)


def test_suspension(coordinator, broker):
    with pytest.raises(services.BrokerError):
        services.set_broker_status(actor=coordinator, broker=broker, status="gone", reason="x")
    with pytest.raises(services.BrokerError):
        services.set_broker_status(actor=coordinator, broker=broker, status="suspended", reason="")
    services.set_broker_status(
        actor=coordinator, broker=broker, status="suspended", reason="No-shows"
    )
    broker.refresh_from_db()
    assert broker.status == BrokerProfile.Status.SUSPENDED
    assert services.active_broker_for(broker.user) is None
