from collections.abc import Callable
from datetime import datetime, timedelta
from io import BytesIO

import pytest
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.utils import timezone
from PIL import Image

from apps.accounts.models import User
from apps.core.roles import Role
from apps.core.sms import LocmemBackend


@pytest.fixture(autouse=True)
def _isolate_state():
    """Rate-limit counters and the SMS outbox must not leak between tests."""
    cache.clear()
    LocmemBackend.outbox.clear()
    yield
    cache.clear()
    LocmemBackend.outbox.clear()


@pytest.fixture
def sms_outbox() -> list:
    return LocmemBackend.outbox


@pytest.fixture
def phone() -> str:
    return "+256772123456"


# --- Managed-viewing fixtures (listings, brokers, bookings, payments, ops) --------

_STAFF_PHONES = iter(f"+2567011{n:05d}" for n in range(100_000))
_CUSTOMER_PHONES = iter(f"+2567781{n:05d}" for n in range(100_000))


def in_hours(hours: float) -> datetime:
    return timezone.now() + timedelta(hours=hours)


@pytest.fixture
def make_staff(db) -> Callable[..., User]:
    def build(*roles: Role, name: str = "Staff") -> User:
        user = User.objects.create_user(
            next(_STAFF_PHONES), password="a-long-staff-password", full_name=name, is_staff=True
        )
        for role in roles:
            user.groups.add(Group.objects.get_or_create(name=role.value)[0])
        return user

    return build


@pytest.fixture
def make_customer(db) -> Callable[..., User]:
    return lambda name="Customer": User.objects.create_user(next(_CUSTOMER_PHONES), full_name=name)


@pytest.fixture
def acquirer(make_staff) -> User:
    return make_staff(Role.ACQUISITION, name="Acquirer")


@pytest.fixture
def verifier(make_staff) -> User:
    return make_staff(Role.VERIFICATION, name="Verifier")


@pytest.fixture
def approver(make_staff) -> User:
    return make_staff(Role.APPROVAL, name="Approver")


@pytest.fixture
def coordinator(make_staff) -> User:
    return make_staff(Role.BOOKINGS, name="Coordinator")


@pytest.fixture
def finance_user(make_staff) -> User:
    return make_staff(Role.FINANCE, name="Finance")


@pytest.fixture
def customer(make_customer) -> User:
    return make_customer("Brian Okello")


@pytest.fixture
def jpeg() -> Callable[[], ContentFile]:
    def build() -> ContentFile:
        buffer = BytesIO()
        Image.new("RGB", (40, 30), (200, 180, 90)).save(buffer, format="JPEG")
        return ContentFile(buffer.getvalue(), name="photo.jpg")

    return build


@pytest.fixture
def details():
    from apps.listings.services import PropertyDetails

    def build(**overrides) -> PropertyDetails:
        values = {
            "title": "Two-bedroom apartment",
            "area": "Ntinda",
            "location_hint": "off Ntinda-Kisaasi Rd",
            "directions": "Blue gate after the Shell station; ask for Joseph",
            "kind": "apartment",
            "rent_ugx": 900_000,
            "bedrooms": 2,
            "bathrooms": 1,
            "furnished": False,
            "description": "Quiet compound with parking.",
        }
        values.update(overrides)
        return PropertyDetails(**values)

    return build


@pytest.fixture
def property_contact(acquirer):
    from apps.listings import services

    return services.create_contact(
        actor=acquirer, name="Joseph Mukasa", phone="0772555111", relationship="owner"
    )


@pytest.fixture
def draft_property(acquirer, property_contact, details, jpeg):
    from apps.listings import services

    prop = services.create_property(
        actor=acquirer, contact=property_contact, details=details(), authority_document=jpeg()
    )
    services.add_photo(actor=acquirer, prop=prop, image=jpeg(), caption="Front")
    return prop


@pytest.fixture
def inspect():
    from apps.listings import services
    from apps.listings.models import CHECKLIST

    def run(prop, verifier, hours_ago: float = 1):
        return services.submit_verification(
            actor=verifier,
            prop=prop,
            checklist=dict.fromkeys(CHECKLIST, True),
            checked_at=in_hours(-hours_ago),
        )

    return run


@pytest.fixture
def published_property(draft_property, verifier, approver, inspect):
    from apps.listings import services

    verification = inspect(draft_property, verifier)
    services.decide_verification(actor=approver, verification=verification, approve=True)
    draft_property.refresh_from_db()
    return draft_property


@pytest.fixture
def make_broker(coordinator):
    from apps.brokers import services

    phones = iter(f"+2567551{n:05d}" for n in range(1000))

    def build(name: str = "Grace Broker"):
        return services.onboard_broker(
            actor=coordinator,
            phone=next(phones),
            full_name=name,
            id_checked_at=in_hours(-48),
            terms_signed_at=in_hours(-47),
            areas=["Ntinda"],
        )

    return build


@pytest.fixture
def broker(make_broker):
    return make_broker()


@pytest.fixture
def requested(customer, published_property):
    from apps.bookings import services

    return services.request_viewing(
        customer=customer, prop=published_property, preferred_start=in_hours(26)
    )


@pytest.fixture
def awaiting_payment(requested, coordinator, broker):
    from apps.bookings import services

    return services.confirm(
        actor=coordinator, appointment=requested, scheduled_start=in_hours(26), broker=broker
    )


@pytest.fixture
def booked(awaiting_payment, finance_user):
    from apps.payments import services

    services.record_manual_payment(
        actor=finance_user,
        appointment=awaiting_payment,
        provider_txn_id="MP2611ABC",
        amount_ugx=awaiting_payment.fee_ugx,
    )
    awaiting_payment.refresh_from_db()
    return awaiting_payment
