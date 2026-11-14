"""Viewing appointments: request -> confirm -> pay -> assigned broker -> report.

Payment confirmation enters through `mark_paid`, which only `payments.services` calls after a
provider-verified event or a finance-recorded transaction. Every transition is audited.
"""

import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record
from apps.brokers.models import BrokerProfile
from apps.core import ratelimit
from apps.core.roles import Role, require_role
from apps.core.tasks import send_sms_task
from apps.listings.models import Property

from .models import (
    OPEN_STATUSES,
    SLOT_HOLDING_STATUSES,
    Appointment,
    Feedback,
    ViewingReport,
)

logger = logging.getLogger(__name__)

Status = Appointment.Status
Reason = Appointment.CancelReason
ONE_DAY = 24 * 60 * 60
CHECK_IN_EARLY = timedelta(minutes=60)
CHECK_IN_LATE = timedelta(hours=3)
NO_SHOW_WAIT = timedelta(minutes=20)  # a broker waits this long past the start before a no-show


class BookingError(Exception):
    """User-facing failure; str(error) is safe to display."""


def _sms(phone: str, body: str) -> None:
    transaction.on_commit(lambda: send_sms_task.delay(phone, body))


def _fmt(moment: datetime) -> str:
    return timezone.localtime(moment).strftime("%a %d %b, %H:%M")


def _lock(appointment: Appointment) -> Appointment:
    return (
        # of=("self",): Postgres can't lock the nullable side of the broker outer join.
        Appointment.objects.select_for_update(of=("self",))
        .select_related("property", "customer", "broker__user")
        .get(pk=appointment.pk)
    )


def _check_start_window(start: datetime) -> None:
    now = timezone.now()
    if start < now + timedelta(hours=settings.APPOINTMENT_MIN_LEAD_HOURS):
        raise BookingError(
            f"Choose a time at least {settings.APPOINTMENT_MIN_LEAD_HOURS} hours from now."
        )
    if start > now + timedelta(days=settings.APPOINTMENT_MAX_LEAD_DAYS):
        raise BookingError(
            f"Choose a time within the next {settings.APPOINTMENT_MAX_LEAD_DAYS} days."
        )


# --- Customer ----------------------------------------------------------------


def request_viewing(
    *, customer: User, prop: Property, preferred_start: datetime, note: str = ""
) -> Appointment:
    if not prop.is_bookable:
        raise BookingError("This home is no longer available for viewings.")
    _check_start_window(preferred_start)
    if not ratelimit.allow(
        f"viewing-request:{customer.pk}", settings.VIEWING_REQUESTS_PER_DAY, ONE_DAY
    ):
        raise BookingError("You've made a lot of requests today. Please try again tomorrow.")

    with transaction.atomic():
        # Serialise one customer's requests so the open-request cap can't be raced.
        User.objects.select_for_update().get(pk=customer.pk)
        open_requests = Appointment.objects.filter(customer=customer, status__in=OPEN_STATUSES)
        if open_requests.filter(property=prop).exists():
            raise BookingError("You already have an open viewing for this home.")
        if open_requests.count() >= settings.MAX_OPEN_REQUESTS_PER_CUSTOMER:
            raise BookingError(
                f"You can have up to {settings.MAX_OPEN_REQUESTS_PER_CUSTOMER} open viewings. "
                "Cancel one or wait until it's done."
            )
        appointment = Appointment.objects.create(
            customer=customer, property=prop, preferred_start=preferred_start, customer_note=note
        )
        record(
            actor=customer,
            action="appointment.requested",
            target=appointment,
            evidence={"property": str(prop.pk), "preferred_start": preferred_start.isoformat()},
        )
        _sms(
            customer.phone,
            f"Crib Link: viewing request {appointment.reference} received. We'll confirm the "
            "time with the property and a broker, then send payment details. Nothing to pay yet.",
        )
    return appointment


def refund_due_on_customer_cancel(appointment: Appointment) -> bool:
    if appointment.status != Status.BOOKED or appointment.scheduled_start is None:
        return False
    cutoff = timedelta(hours=settings.CANCELLATION_REFUND_CUTOFF_HOURS)
    return appointment.scheduled_start - timezone.now() >= cutoff


def cancel_by_customer(*, customer: User, appointment: Appointment) -> Appointment:
    from apps.payments import services as payments

    with transaction.atomic():
        appointment = _lock(appointment)
        if appointment.customer_id != customer.pk:
            raise PermissionDenied("Not your appointment.")
        if not appointment.is_open:
            raise BookingError("This viewing can no longer be cancelled.")
        refund = refund_due_on_customer_cancel(appointment)
        was_booked = appointment.status == Status.BOOKED
        _close(appointment, Status.CANCELLED, Reason.CUSTOMER, "", customer)
        if refund:
            payments.request_refund(
                appointment=appointment, actor=None, reason="Customer cancelled before the cutoff"
            )
        record(
            actor=customer,
            action="appointment.cancelled",
            target=appointment,
            evidence={"by": "customer", "refund": refund},
        )
        _notify_cancelled(appointment, refund=refund, notify_broker=was_booked)
    return appointment


def leave_feedback(
    *, customer: User, appointment: Appointment, rating: int, comment: str, may_publish: bool
) -> Feedback:
    if appointment.customer_id != customer.pk:
        raise PermissionDenied("Not your appointment.")
    if appointment.status != Status.COMPLETED:
        raise BookingError("You can leave feedback after the viewing.")
    if not 1 <= rating <= 5:
        raise BookingError("Rate from 1 to 5.")
    try:
        with transaction.atomic():
            feedback = Feedback.objects.create(
                appointment=appointment, rating=rating, comment=comment, may_publish=may_publish
            )
    except IntegrityError as exc:  # one per appointment, even on a double submit
        raise BookingError("Thanks, we already have your feedback.") from exc
    with transaction.atomic():
        record(
            actor=customer,
            action="appointment.feedback",
            target=appointment,
            evidence={"rating": rating},
        )
    return feedback


# --- Booking coordinator --------------------------------------------------------


def broker_is_free(
    broker: BrokerProfile, start: datetime, *, exclude: Appointment | None = None
) -> bool:
    slot = timedelta(minutes=settings.APPOINTMENT_SLOT_MINUTES)
    clashes = Appointment.objects.filter(
        broker=broker,
        status__in=SLOT_HOLDING_STATUSES,
        scheduled_start__gt=start - slot,
        scheduled_start__lt=start + slot,
    )
    if exclude is not None:
        clashes = clashes.exclude(pk=exclude.pk)
    return not clashes.exists()


def _claim_broker(broker: BrokerProfile, start: datetime, exclude: Appointment) -> BrokerProfile:
    broker = BrokerProfile.objects.select_for_update().select_related("user").get(pk=broker.pk)
    if not broker.is_active:
        raise BookingError("That broker is not active.")
    if not broker_is_free(broker, start, exclude=exclude):
        raise BookingError("That broker already has a viewing at that time.")
    return broker


def _payment_due(start: datetime) -> datetime:
    window_end = timezone.now() + timedelta(hours=settings.PAYMENT_WINDOW_HOURS)
    before_start = start - timedelta(hours=settings.PAYMENT_CLOSE_BEFORE_START_HOURS)
    return min(window_end, before_start)


def confirm(
    *, actor: User, appointment: Appointment, scheduled_start: datetime, broker: BrokerProfile
) -> Appointment:
    """Property access and broker are both confirmed for this slot; ask the customer to pay."""
    from apps.payments.services import payment_instructions

    require_role(actor, Role.BOOKINGS)
    _check_start_window(scheduled_start)
    with transaction.atomic():
        appointment = _lock(appointment)
        if appointment.status != Status.REQUESTED:
            raise BookingError("Only new requests can be confirmed.")
        if not appointment.property.is_bookable:
            raise BookingError("The listing is no longer published; decline the request.")
        broker = _claim_broker(broker, scheduled_start, appointment)

        now = timezone.now()
        appointment.status = Status.AWAITING_PAYMENT
        appointment.scheduled_start = scheduled_start
        appointment.broker = broker
        appointment.confirmed_by = actor
        appointment.confirmed_at = now
        appointment.fee_ugx = settings.VIEWING_FEE_UGX
        appointment.payment_due_at = _payment_due(scheduled_start)
        appointment.save()
        record(
            actor=actor,
            action="appointment.confirmed",
            target=appointment,
            evidence={
                "scheduled_start": scheduled_start.isoformat(),
                "broker": str(broker.pk),
                "fee_ugx": appointment.fee_ugx,
            },
        )
        _sms(
            appointment.customer.phone,
            f"Crib Link: {appointment.reference} is available on {_fmt(scheduled_start)}. "
            f"To secure it, pay UGX {appointment.fee_ugx:,} by {_fmt(appointment.payment_due_at)}. "
            f"{payment_instructions(appointment)}",
        )
    return appointment


def decline(*, actor: User, appointment: Appointment, reason: str, note: str = "") -> Appointment:
    require_role(actor, Role.BOOKINGS)
    if reason not in Reason.values or reason == Reason.CUSTOMER:
        raise BookingError("Pick a reason.")
    with transaction.atomic():
        appointment = _lock(appointment)
        if appointment.status != Status.REQUESTED:
            raise BookingError("Only new requests can be declined.")
        _close(appointment, Status.DECLINED, reason, note, actor)
        record(
            actor=actor,
            action="appointment.declined",
            target=appointment,
            evidence={"reason": reason, "note": note},
        )
        _sms(
            appointment.customer.phone,
            f"Crib Link: sorry, we couldn't arrange {appointment.reference} "
            f"({Reason(reason).label.lower()}). You have not been charged.",
        )
    return appointment


def cancel_by_staff(
    *, actor: User, appointment: Appointment, reason: str, note: str = ""
) -> Appointment:
    """Staff-side cancellation (property gone, broker no-show...). A paid booking is refunded."""
    from apps.payments import services as payments

    require_role(actor, Role.BOOKINGS)
    if reason not in Reason.values:
        raise BookingError("Pick a reason.")
    with transaction.atomic():
        appointment = _lock(appointment)
        if not appointment.is_open:
            raise BookingError("This viewing is already closed.")
        was_booked = appointment.status == Status.BOOKED
        _close(appointment, Status.CANCELLED, reason, note, actor)
        if was_booked:
            payments.request_refund(
                appointment=appointment, actor=actor, reason=Reason(reason).label
            )
        record(
            actor=actor,
            action="appointment.cancelled",
            target=appointment,
            evidence={"by": "staff", "reason": reason, "note": note, "refund": was_booked},
        )
        _notify_cancelled(appointment, refund=was_booked, notify_broker=was_booked)
    return appointment


def reschedule(
    *, actor: User, appointment: Appointment, scheduled_start: datetime, broker: BrokerProfile
) -> Appointment:
    require_role(actor, Role.BOOKINGS)
    _check_start_window(scheduled_start)
    with transaction.atomic():
        appointment = _lock(appointment)
        if appointment.status not in SLOT_HOLDING_STATUSES:
            raise BookingError("Only confirmed or booked viewings can be rescheduled.")
        previous = {
            "scheduled_start": appointment.scheduled_start.isoformat(),
            "broker": str(appointment.broker_id),
        }
        old_broker = appointment.broker
        broker = _claim_broker(broker, scheduled_start, appointment)
        appointment.scheduled_start = scheduled_start
        appointment.broker = broker
        appointment.reminder_sent_at = None
        appointment.checked_in_at = None
        if appointment.status == Status.AWAITING_PAYMENT:
            appointment.payment_due_at = _payment_due(scheduled_start)
        appointment.save()
        record(
            actor=actor,
            action="appointment.rescheduled",
            target=appointment,
            evidence={
                "from": previous,
                "to_start": scheduled_start.isoformat(),
                "to_broker": str(broker.pk),
            },
        )
        _sms(
            appointment.customer.phone,
            f"Crib Link: {appointment.reference} is now on {_fmt(scheduled_start)}.",
        )
        if appointment.status == Status.BOOKED:
            if old_broker and old_broker.pk != broker.pk:
                _sms(old_broker.user.phone, f"Crib Link: {appointment.reference} was reassigned.")
            _sms(broker.user.phone, _broker_assignment_text(appointment))
    return appointment


# --- Payment hook (called by payments.services only) -------------------------------


def mark_paid(*, appointment: Appointment) -> bool:
    """Called inside the payment transaction. True if the booking is now secured; False if
    the slot was already released or paid (the caller then refunds the money)."""
    appointment = _lock(appointment)
    if appointment.status != Status.AWAITING_PAYMENT:
        return False
    appointment.status = Status.BOOKED
    appointment.paid_at = timezone.now()
    appointment.save(update_fields=["status", "paid_at", "updated_at"])
    record(actor=None, action="appointment.booked", target=appointment)
    _sms(
        appointment.customer.phone,
        f"Crib Link: payment received, {appointment.reference} is booked for "
        f"{_fmt(appointment.scheduled_start)}. Your broker is {broker_name(appointment)}; "
        "they will meet you there.",
    )
    _sms(appointment.broker.user.phone, _broker_assignment_text(appointment))
    return True


def broker_name(appointment: Appointment) -> str:
    """Never fall back to the broker's phone number (User.__str__ would)."""
    return appointment.broker.user.full_name or "a Crib Link broker"


def _broker_assignment_text(appointment: Appointment) -> str:
    return (
        f"Crib Link: you're assigned {appointment.reference} at "
        f"{_fmt(appointment.scheduled_start)}, {appointment.property.area}. "
        "Open the broker page for the customer and directions."
    )


# --- Broker -------------------------------------------------------------------------


def _require_assigned_broker(user: User, appointment: Appointment) -> BrokerProfile:
    broker = getattr(user, "broker_profile", None)
    if broker is None or not broker.is_active or appointment.broker_id != broker.pk:
        raise PermissionDenied("This viewing isn't assigned to you.")
    return broker


def check_in(*, user: User, appointment: Appointment) -> Appointment:
    with transaction.atomic():
        appointment = _lock(appointment)
        _require_assigned_broker(user, appointment)
        if appointment.status != Status.BOOKED:
            raise BookingError("Only booked viewings can be checked in.")
        now = timezone.now()
        start = appointment.scheduled_start
        if not start - CHECK_IN_EARLY <= now <= start + CHECK_IN_LATE:
            raise BookingError("Check in when you arrive for the viewing.")
        if appointment.checked_in_at is None:
            appointment.checked_in_at = now
            appointment.save(update_fields=["checked_in_at", "updated_at"])
            record(actor=user, action="appointment.checked_in", target=appointment)
    return appointment


def submit_report(
    *,
    user: User,
    appointment: Appointment,
    customer_attended: bool,
    property_as_listed: bool,
    customer_interest: str,
    notes: str = "",
    discrepancy_note: str = "",
) -> ViewingReport:
    if customer_interest not in ViewingReport.Interest.values:
        raise BookingError("Pick the customer's interest.")
    if not property_as_listed and not discrepancy_note.strip():
        raise BookingError("Describe what didn't match the listing.")
    with transaction.atomic():
        appointment = _lock(appointment)
        broker = _require_assigned_broker(user, appointment)
        if appointment.status != Status.BOOKED:
            raise BookingError("This viewing is not open for a report.")
        if appointment.checked_in_at is None:
            raise BookingError("Check in before filing the report.")
        if not customer_attended and timezone.now() < appointment.scheduled_start + NO_SHOW_WAIT:
            raise BookingError(
                f"Wait until {NO_SHOW_WAIT.seconds // 60} minutes after the start before "
                "reporting a no-show."
            )
        report = ViewingReport.objects.create(
            appointment=appointment,
            broker=broker,
            customer_attended=customer_attended,
            property_as_listed=property_as_listed,
            discrepancy_note=discrepancy_note,
            customer_interest=customer_interest if customer_attended else "na",
            notes=notes,
        )
        appointment.status = Status.COMPLETED if customer_attended else Status.CUSTOMER_NO_SHOW
        appointment.completed_at = timezone.now()
        appointment.save(update_fields=["status", "completed_at", "updated_at"])
        record(
            actor=user,
            action="appointment.reported",
            target=appointment,
            evidence={"attended": customer_attended, "as_listed": property_as_listed},
        )
        if not property_as_listed:
            record(
                actor=user,
                action="property.flagged",
                target=appointment.property,
                evidence={"appointment": appointment.reference, "note": discrepancy_note},
            )
        if customer_attended:
            _sms(
                appointment.customer.phone,
                f"Crib Link: thanks for viewing with us ({appointment.reference}). Tell us how "
                "it went, or reply to ask for similar homes.",
            )
    return report


# --- Shared helpers and scheduled jobs ---------------------------------------------


def _close(
    appointment: Appointment, status: str, reason: str, note: str, actor: User | None
) -> None:
    appointment.status = status
    appointment.closed_reason = reason
    appointment.closed_note = note
    appointment.closed_by = actor
    appointment.closed_at = timezone.now()
    appointment.save()


def _notify_cancelled(appointment: Appointment, *, refund: bool, notify_broker: bool) -> None:
    money = " Your fee will be refunded." if refund else ""
    _sms(appointment.customer.phone, f"Crib Link: {appointment.reference} is cancelled.{money}")
    if notify_broker and appointment.broker is not None:
        _sms(appointment.broker.user.phone, f"Crib Link: {appointment.reference} is cancelled.")


def lapse_unpaid_appointments() -> int:
    lapsed = 0
    due = Appointment.objects.filter(
        status=Status.AWAITING_PAYMENT, payment_due_at__lte=timezone.now()
    )
    for candidate in due.iterator():
        with transaction.atomic():
            appointment = _lock(candidate)
            if appointment.status != Status.AWAITING_PAYMENT:
                continue
            _close(appointment, Status.LAPSED, "", "Payment window passed", None)
            record(actor=None, action="appointment.lapsed", target=appointment)
            _sms(
                appointment.customer.phone,
                f"Crib Link: {appointment.reference} was released because it wasn't paid in "
                "time. You can request a new viewing any time.",
            )
        lapsed += 1
    return lapsed


def send_reminders() -> int:
    now = timezone.now()
    due = Appointment.objects.filter(
        status=Status.BOOKED,
        reminder_sent_at__isnull=True,
        scheduled_start__gt=now,
        scheduled_start__lte=now + timedelta(hours=settings.REMINDER_BEFORE_START_HOURS),
    ).select_related("customer", "broker__user", "property")
    sent = 0
    for appointment in due:
        with transaction.atomic():
            updated = Appointment.objects.filter(
                pk=appointment.pk, reminder_sent_at__isnull=True
            ).update(reminder_sent_at=now)
            if not updated:
                continue
            when = _fmt(appointment.scheduled_start)
            _sms(
                appointment.customer.phone,
                f"Crib Link reminder: {appointment.reference} at {when}.",
            )
            _sms(
                appointment.broker.user.phone,
                f"Crib Link reminder: {appointment.reference} at {when}.",
            )
        sent += 1
    return sent
