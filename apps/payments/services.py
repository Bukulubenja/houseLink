"""Payments, refunds and reconciliation.

- An appointment becomes BOOKED only through `_apply_success`: a provider-signed webhook, or
  finance recording a transaction they can see on the merchant statement. Never a redirect.
- Money that arrives for a slot already released (or paid twice) is refunded automatically.
- Separation of duties: whoever recorded a manual payment cannot reconcile it, and whoever
  requested a refund cannot mark it paid.
"""

import logging

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record
from apps.bookings import services as bookings
from apps.bookings.models import Appointment
from apps.core.crypto import sha256_hex
from apps.core.roles import Role, require_role
from apps.core.tasks import send_sms_task

from .models import Payment, Refund, WebhookEvent
from .providers import Checkout, ProviderEvent, WebhookError, get_provider

logger = logging.getLogger(__name__)

PStatus = Payment.Status
AStatus = Appointment.Status
CURRENCY = "UGX"


class PaymentError(Exception):
    """User-facing failure; str(error) is safe to display."""


def payment_instructions(appointment: Appointment) -> str:
    return get_provider().instructions(appointment.reference)


def start_checkout(*, customer: User, appointment: Appointment) -> Checkout:
    provider = get_provider()
    if appointment.customer_id != customer.pk:
        raise PermissionDenied("Not your appointment.")
    if appointment.status != AStatus.AWAITING_PAYMENT:
        raise PaymentError("This viewing isn't waiting for payment.")
    if not provider.has_checkout:
        return Checkout(None, provider.instructions(appointment.reference))
    with transaction.atomic():
        payment = (
            Payment.objects.select_for_update()
            .filter(
                appointment=appointment,
                provider=provider.name,
                status=PStatus.PENDING,
                amount_ugx=appointment.fee_ugx,
            )
            .first()
        )
        if payment is None:
            payment = Payment.objects.create(
                appointment=appointment, provider=provider.name, amount_ugx=appointment.fee_ugx
            )
            record(actor=customer, action="payment.started", target=payment)
    return provider.checkout(payment)


def _lock_appointment(appointment_id: object) -> Appointment:
    """Lock order everywhere: Appointment first, then Payment/Refund (avoids deadlocks with
    cancellations, which lock the appointment and then request a refund)."""
    return (
        Appointment.objects.select_for_update(of=("self",))
        .select_related("customer")
        .get(pk=appointment_id)
    )


def _apply_success(
    payment: Payment, *, amount_ugx: int, provider_txn_id: str, actor: User | None
) -> Payment:
    """Inside a transaction, with the appointment and then `payment` locked."""
    if payment.status in {PStatus.SUCCEEDED, PStatus.MISMATCH}:
        return payment
    payment.provider_txn_id = provider_txn_id
    payment.amount_received_ugx = amount_ugx
    payment.confirmed_at = timezone.now()
    if amount_ugx != payment.amount_ugx:
        payment.status = PStatus.MISMATCH
        payment.save()
        record(
            actor=actor,
            action="payment.mismatch",
            target=payment,
            evidence={"expected": payment.amount_ugx, "received": amount_ugx},
        )
        logger.warning("Payment %s amount mismatch", payment.reference)
        return payment
    payment.status = PStatus.SUCCEEDED
    payment.save()
    record(
        actor=actor,
        action="payment.succeeded",
        target=payment,
        evidence={"provider": payment.provider, "txn": provider_txn_id, "amount": amount_ugx},
    )
    _book_or_refund(payment)
    return payment


def _book_or_refund(payment: Payment) -> None:
    if not bookings.mark_paid(appointment=payment.appointment):
        # The recorder counts as the requester, so they can't also pay the refund out.
        _create_refund(
            payment,
            actor=payment.recorded_by,
            reason="Paid after the slot was released or already paid",
        )


def handle_webhook(*, body: bytes, meta: dict) -> str:
    """Verify, de-duplicate and apply one provider event. Returns an outcome label."""
    provider = get_provider()
    event: ProviderEvent = provider.parse_webhook(body, meta)  # raises WebhookError
    if event.currency != CURRENCY:
        raise WebhookError("Unexpected currency.")
    with transaction.atomic():
        try:
            with transaction.atomic():
                stored = WebhookEvent.objects.create(
                    provider=provider.name,
                    event_id=event.event_id,
                    body_sha256=sha256_hex(body),
                    outcome="received",
                )
        except IntegrityError:
            return "duplicate"

        found = Payment.objects.filter(reference=event.reference, provider=provider.name).first()
        if found is None:
            outcome = "unknown_reference"
            logger.warning("Webhook for unknown payment reference")
        else:
            _lock_appointment(found.appointment_id)
            payment = Payment.objects.select_for_update().get(pk=found.pk)
            outcome = _apply_event(payment, event)
        stored.outcome = outcome
        stored.save(update_fields=["outcome"])
    return outcome


def _apply_event(payment: Payment, event: ProviderEvent) -> str:
    if event.succeeded:
        try:
            with transaction.atomic():
                payment = _apply_success(
                    payment,
                    amount_ugx=event.amount_ugx,
                    provider_txn_id=event.provider_txn_id,
                    actor=None,
                )
        except IntegrityError:
            logger.warning("Webhook repeated an already-recorded provider transaction")
            return "duplicate_txn"
        return payment.status
    if payment.status == PStatus.PENDING:
        payment.status = PStatus.FAILED
        payment.save(update_fields=["status", "updated_at"])
        record(actor=None, action="payment.failed", target=payment)
        return "failed"
    return "ignored"


def record_manual_payment(
    *,
    actor: User,
    appointment: Appointment,
    provider_txn_id: str,
    amount_ugx: int,
    amount_differs: bool = False,
) -> Payment:
    """Finance saw this transaction on the merchant statement, quoting the reference.

    An amount other than the fee is refused unless `amount_differs` confirms the customer
    really paid that; it is then held as MISMATCH for a second person to settle."""
    require_role(actor, Role.FINANCE)
    provider_txn_id = provider_txn_id.strip()
    if not provider_txn_id:
        raise PaymentError("Enter the provider's transaction ID.")
    if appointment.fee_ugx is None:
        raise PaymentError("This viewing was never confirmed, so no fee is due.")
    if amount_ugx != appointment.fee_ugx and not amount_differs:
        raise PaymentError(
            f"The fee is UGX {appointment.fee_ugx:,}. If the statement really shows a "
            "different amount, tick 'amount differs' to record it for review."
        )
    try:
        with transaction.atomic():
            _lock_appointment(appointment.pk)
            payment = Payment.objects.create(
                appointment=appointment,
                provider="manual",
                amount_ugx=appointment.fee_ugx,
                recorded_by=actor,
            )
            payment = Payment.objects.select_for_update().get(pk=payment.pk)
            return _apply_success(
                payment, amount_ugx=amount_ugx, provider_txn_id=provider_txn_id, actor=actor
            )
    except IntegrityError as exc:
        raise PaymentError("That transaction ID has already been recorded.") from exc


def resolve_mismatch(*, actor: User, payment: Payment, accept: bool, note: str) -> Payment:
    """A second finance person settles a payment whose amount differed from the fee:
    accept it as the fee (books the viewing) or refund what was received."""
    require_role(actor, Role.FINANCE)
    if not note.strip():
        raise PaymentError("Say why.")
    with transaction.atomic():
        _lock_appointment(payment.appointment_id)
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.status != PStatus.MISMATCH:
            raise PaymentError("This payment has no amount mismatch to resolve.")
        if payment.recorded_by_id == actor.pk:
            raise PermissionDenied("Someone else must resolve a payment you recorded.")
        payment.status = PStatus.SUCCEEDED
        payment.save(update_fields=["status", "updated_at"])
        record(
            actor=actor,
            action="payment.mismatch_accepted" if accept else "payment.mismatch_refunded",
            target=payment,
            evidence={"note": note, "received": payment.amount_received_ugx},
        )
        if accept:
            _book_or_refund(payment)
        else:
            _create_refund(payment, actor=actor, reason=f"Wrong amount paid: {note}")
    return payment


def _create_refund(payment: Payment, *, actor: User | None, reason: str) -> Refund | None:
    if Refund.objects.filter(payment=payment).exists():
        return None
    refund = Refund.objects.create(
        payment=payment,
        amount_ugx=payment.amount_received_ugx or payment.amount_ugx,
        reason=reason,
        requested_by=actor,
    )
    record(actor=actor, action="refund.requested", target=refund, evidence={"reason": reason})
    return refund


def request_refund(*, appointment: Appointment, actor: User | None, reason: str) -> Refund | None:
    """Refund the appointment's successful payment, if any. Called by bookings, which has
    already locked the appointment."""
    payment = (
        Payment.objects.select_for_update(of=("self",))
        .filter(appointment=appointment, status=PStatus.SUCCEEDED, refund__isnull=True)
        .order_by("confirmed_at")
        .first()
    )
    if payment is None:
        return None
    return _create_refund(payment, actor=actor, reason=reason)


def mark_refund_paid(*, actor: User, refund: Refund, payout_ref: str) -> Refund:
    require_role(actor, Role.FINANCE)
    if not payout_ref.strip():
        raise PaymentError("Enter the payout transaction reference.")
    with transaction.atomic():
        current = Refund.objects.select_related("payment").get(pk=refund.pk)
        appointment = _lock_appointment(current.payment.appointment_id)
        refund = (
            Refund.objects.select_for_update(of=("self",))
            .select_related("payment")
            .get(pk=refund.pk)
        )
        if refund.status != Refund.Status.REQUESTED:
            raise PaymentError("This refund is already paid.")
        if actor.pk in {refund.requested_by_id, refund.payment.recorded_by_id}:
            raise PermissionDenied(
                "Someone else must pay out a refund you requested or whose payment you recorded."
            )
        refund.status = Refund.Status.PAID
        refund.paid_by = actor
        refund.paid_at = timezone.now()
        refund.payout_ref = payout_ref.strip()
        refund.save()
        record(
            actor=actor, action="refund.paid", target=refund, evidence={"payout_ref": payout_ref}
        )
        body = (
            f"Crib Link: UGX {refund.amount_ugx:,} for {appointment.reference} has been "
            f"refunded (ref {refund.payout_ref})."
        )
        phone = appointment.customer.phone
        transaction.on_commit(lambda: send_sms_task.delay(phone, body))
    return refund


def reconcile(*, actor: User, payment: Payment, statement_ref: str) -> Payment:
    require_role(actor, Role.FINANCE)
    if not statement_ref.strip():
        raise PaymentError("Enter the statement line reference.")
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.status != PStatus.SUCCEEDED:
            raise PaymentError("Only successful payments are reconciled.")
        if payment.reconciled_at is not None:
            raise PaymentError("Already reconciled.")
        if payment.recorded_by_id == actor.pk:
            raise PermissionDenied("Someone else must reconcile a payment you recorded.")
        payment.reconciled_at = timezone.now()
        payment.reconciled_by = actor
        payment.statement_ref = statement_ref.strip()
        payment.save()
        record(
            actor=actor,
            action="payment.reconciled",
            target=payment,
            evidence={"statement_ref": payment.statement_ref},
        )
    return payment
