from datetime import datetime

from django.conf import settings
from django.db import transaction

from apps.accounts.models import User
from apps.audit.services import record
from apps.bookings import services as bookings
from apps.core.phone import normalize_phone
from apps.core.roles import Role, require_role

from .models import BrokerProfile


class BrokerError(Exception):
    """User-facing failure; str(error) is safe to display."""


def onboard_broker(
    *,
    actor: User,
    phone: str,
    full_name: str,
    id_checked_at: datetime,
    terms_signed_at: datetime,
    areas: list[str],
) -> BrokerProfile:
    """Brokers sign in by SMS code, so the account is a normal phone account."""
    require_role(actor, Role.BOOKINGS)
    unknown = set(areas) - set(settings.SERVICE_AREAS)
    if unknown:
        raise BrokerError("Some areas are outside the service area.")
    phone = normalize_phone(phone)
    with transaction.atomic():
        user = User.objects.select_for_update().filter(phone=phone).first()
        if user is None:
            user = User.objects.create_user(phone, full_name=full_name)
        elif not user.full_name:
            user.full_name = full_name
            user.save(update_fields=["full_name"])
        if user.is_staff:
            raise BrokerError("Staff accounts can't also be brokers; use a separate number.")
        if not user.is_active:
            raise BrokerError("This account is suspended.")
        if BrokerProfile.objects.filter(user=user).exists():
            raise BrokerError("This person is already a broker.")
        broker = BrokerProfile.objects.create(
            user=user,
            id_checked_at=id_checked_at,
            terms_signed_at=terms_signed_at,
            areas=areas,
            onboarded_by=actor,
        )
        record(actor=actor, action="broker.onboarded", target=broker, evidence={"areas": areas})
    return broker


def set_broker_status(
    *, actor: User, broker: BrokerProfile, status: str, reason: str
) -> BrokerProfile:
    require_role(actor, Role.BOOKINGS)
    if status not in BrokerProfile.Status.values:
        raise BrokerError("Unknown status.")
    if not reason.strip():
        raise BrokerError("Give a reason.")
    with transaction.atomic():
        broker = BrokerProfile.objects.select_for_update().get(pk=broker.pk)
        previous = broker.status
        broker.status = status
        broker.save(update_fields=["status", "updated_at"])
        flagged = 0
        if status == BrokerProfile.Status.SUSPENDED:
            flagged = bookings.flag_for_new_broker(broker=broker, actor=actor)
        record(
            actor=actor,
            action=f"broker.{status}",
            target=broker,
            evidence={"previous": previous, "reason": reason, "viewings_flagged": flagged},
        )
    return broker


def active_broker_for(user: User) -> BrokerProfile | None:
    broker = getattr(user, "broker_profile", None) if user.is_authenticated else None
    return broker if broker is not None and broker.is_active else None
