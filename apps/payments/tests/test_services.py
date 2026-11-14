import json
import time
from datetime import timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.test import override_settings
from django.utils import timezone

from apps.bookings.models import Appointment
from apps.bookings.services import lapse_unpaid_appointments
from apps.payments import providers, services
from apps.payments.models import Payment, Refund, WebhookEvent

pytestmark = pytest.mark.django_db(transaction=True)  # on_commit SMS must fire
SECRET = "test-webhook-secret"
DEV = override_settings(PAYMENT_PROVIDER="dev")


def signed(payload: dict, *, secret: str = SECRET, ts: int | None = None) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    ts = int(time.time()) if ts is None else ts
    header = f"t={ts},v1={providers.sign(secret, ts, body)}"
    return body, {providers.SIGNATURE_HEADER: header}


def event(payment: Payment, **overrides) -> dict:
    data = {
        "id": "evt_1",
        "reference": payment.reference,
        "status": "succeeded",
        "amount": payment.amount_ugx,
        "currency": "UGX",
        "transaction_id": "MTN123",
    }
    data.update(overrides)
    return data


@pytest.fixture
def pending_payment(awaiting_payment, customer):
    with DEV:
        checkout = services.start_checkout(customer=customer, appointment=awaiting_payment)
        assert checkout.redirect_url
    return Payment.objects.get()


# --- Manual (pilot) -------------------------------------------------------------


def test_manual_checkout_gives_instructions(awaiting_payment, customer):
    checkout = services.start_checkout(customer=customer, appointment=awaiting_payment)
    assert checkout.redirect_url is None
    assert awaiting_payment.reference in checkout.instructions
    assert not Payment.objects.exists()


def test_checkout_rules(requested, awaiting_payment, customer, make_customer):
    with pytest.raises(PermissionDenied):
        services.start_checkout(customer=make_customer(), appointment=awaiting_payment)
    with pytest.raises(services.PaymentError):
        services.start_checkout(customer=customer, appointment=requested)


def test_manual_payment_books(booked):
    payment = Payment.objects.get()
    assert payment.status == Payment.Status.SUCCEEDED
    assert payment.provider == "manual"
    assert booked.status == Appointment.Status.BOOKED


def test_manual_payment_rules(awaiting_payment, finance_user, coordinator, booked):
    with pytest.raises(PermissionDenied):
        services.record_manual_payment(
            actor=coordinator, appointment=awaiting_payment, provider_txn_id="X", amount_ugx=1
        )
    with pytest.raises(services.PaymentError, match="transaction ID"):
        services.record_manual_payment(
            actor=finance_user, appointment=awaiting_payment, provider_txn_id=" ", amount_ugx=1
        )
    with pytest.raises(services.PaymentError, match="already been recorded"):
        services.record_manual_payment(
            actor=finance_user, appointment=booked, provider_txn_id="MP2611ABC", amount_ugx=44_000
        )


def test_manual_payment_for_unconfirmed_request_refused(requested, finance_user):
    with pytest.raises(services.PaymentError, match="never confirmed"):
        services.record_manual_payment(
            actor=finance_user, appointment=requested, provider_txn_id="X1", amount_ugx=44_000
        )


def test_duplicate_payment_is_refunded(booked, finance_user):
    services.record_manual_payment(
        actor=finance_user, appointment=booked, provider_txn_id="MP-SECOND", amount_ugx=44_000
    )
    refund = Refund.objects.get()
    assert refund.payment.provider_txn_id == "MP-SECOND"


def test_payment_after_lapse_is_refunded_not_booked(awaiting_payment, finance_user):
    Appointment.objects.filter(pk=awaiting_payment.pk).update(
        payment_due_at=timezone.now() - timedelta(minutes=1)
    )
    lapse_unpaid_appointments()
    services.record_manual_payment(
        actor=finance_user, appointment=awaiting_payment, provider_txn_id="LATE1", amount_ugx=44_000
    )
    awaiting_payment.refresh_from_db()
    assert awaiting_payment.status == Appointment.Status.LAPSED
    assert Refund.objects.get().reason.startswith("Paid after")


def test_wrong_amount_is_held_for_finance(awaiting_payment, finance_user):
    with pytest.raises(services.PaymentError, match="amount differs"):
        services.record_manual_payment(
            actor=finance_user,
            appointment=awaiting_payment,
            provider_txn_id="LOW1",
            amount_ugx=4_400,
        )  # a typo is refused, and the transaction ID stays free to record correctly
    payment = services.record_manual_payment(
        actor=finance_user,
        appointment=awaiting_payment,
        provider_txn_id="LOW1",
        amount_ugx=40_000,
        amount_differs=True,
    )
    awaiting_payment.refresh_from_db()
    assert payment.status == Payment.Status.MISMATCH
    assert awaiting_payment.status == Appointment.Status.AWAITING_PAYMENT


# --- Signed webhook ---------------------------------------------------------------


@DEV
def test_checkout_reuses_pending_payment(pending_payment, awaiting_payment, customer):
    services.start_checkout(customer=customer, appointment=awaiting_payment)
    assert Payment.objects.count() == 1


@DEV
def test_valid_webhook_books(pending_payment):
    body, meta = signed(event(pending_payment))
    assert services.handle_webhook(body=body, meta=meta) == "succeeded"
    pending_payment.refresh_from_db()
    assert pending_payment.status == Payment.Status.SUCCEEDED
    assert pending_payment.appointment.status == Appointment.Status.BOOKED


@DEV
def test_replayed_event_is_ignored(pending_payment):
    body, meta = signed(event(pending_payment))
    services.handle_webhook(body=body, meta=meta)
    assert services.handle_webhook(body=body, meta=meta) == "duplicate"
    assert WebhookEvent.objects.count() == 1


@DEV
@pytest.mark.parametrize(
    "tamper",
    [
        lambda p: signed(event(p), secret="wrong"),
        lambda p: signed(event(p), ts=int(time.time()) - 3600),
        lambda p: (json.dumps(event(p)).encode(), {}),
        lambda p: (b"not json", {providers.SIGNATURE_HEADER: "t=1,v1=abc"}),
        lambda p: signed({"id": "x"}),
        lambda p: signed(event(p, currency="USD")),
    ],
)
def test_bad_webhooks_are_rejected(pending_payment, tamper):
    body, meta = tamper(pending_payment)
    with pytest.raises(providers.WebhookError):
        services.handle_webhook(body=body, meta=meta)
    pending_payment.refresh_from_db()
    assert pending_payment.status == Payment.Status.PENDING


@DEV
def test_failed_event_marks_payment_failed(pending_payment):
    body, meta = signed(event(pending_payment, status="failed"))
    assert services.handle_webhook(body=body, meta=meta) == "failed"
    body, meta = signed(event(pending_payment, id="evt_2", status="failed"))
    assert services.handle_webhook(body=body, meta=meta) == "ignored"


@DEV
def test_unknown_reference(pending_payment):
    body, meta = signed(event(pending_payment, reference="PNOPE"))
    assert services.handle_webhook(body=body, meta=meta) == "unknown_reference"


@DEV
def test_webhook_amount_mismatch(pending_payment):
    body, meta = signed(event(pending_payment, amount=1))
    assert services.handle_webhook(body=body, meta=meta) == "mismatch"


def test_manual_provider_has_no_webhook():
    with pytest.raises(providers.WebhookError):
        services.handle_webhook(body=b"{}", meta={})


def test_unknown_provider_and_missing_secret():
    with override_settings(PAYMENT_PROVIDER="paypal"), pytest.raises(ImproperlyConfigured):
        providers.get_provider()
    with override_settings(PAYMENT_PROVIDER="dev", PAYMENT_WEBHOOK_SECRET=""):
        body, meta = signed({"id": "1"})
        with pytest.raises(ImproperlyConfigured):
            services.handle_webhook(body=body, meta=meta)


# --- Refunds and reconciliation (separation of duties) -------------------------------


def test_refund_payout_by_someone_else(booked, coordinator, make_staff, finance_user, sms_outbox):
    from apps.bookings.services import cancel_by_staff
    from apps.core.roles import Role

    both = make_staff(Role.BOOKINGS, Role.FINANCE)
    cancel_by_staff(actor=both, appointment=booked, reason="property_unavailable")
    refund = Refund.objects.get()
    with pytest.raises(PermissionDenied, match="Someone else"):
        services.mark_refund_paid(actor=both, refund=refund, payout_ref="R1")
    with pytest.raises(PermissionDenied):
        services.mark_refund_paid(actor=coordinator, refund=refund, payout_ref="R1")
    with pytest.raises(PermissionDenied, match="whose payment you recorded"):
        services.mark_refund_paid(actor=finance_user, refund=refund, payout_ref="R1")
    other = make_staff(Role.FINANCE)
    with pytest.raises(services.PaymentError):
        services.mark_refund_paid(actor=other, refund=refund, payout_ref=" ")
    services.mark_refund_paid(actor=other, refund=refund, payout_ref="R1")
    refund.refresh_from_db()
    assert refund.status == Refund.Status.PAID
    assert any("refunded" in m.body for m in sms_outbox)
    with pytest.raises(services.PaymentError, match="already paid"):
        services.mark_refund_paid(actor=other, refund=refund, payout_ref="R2")


def test_reconcile_by_someone_else(booked, finance_user, make_staff):
    from apps.core.roles import Role

    payment = Payment.objects.get()
    with pytest.raises(PermissionDenied, match="Someone else"):
        services.reconcile(actor=finance_user, payment=payment, statement_ref="L1")
    other = make_staff(Role.FINANCE)
    with pytest.raises(services.PaymentError):
        services.reconcile(actor=other, payment=payment, statement_ref="")
    services.reconcile(actor=other, payment=payment, statement_ref="L1")
    with pytest.raises(services.PaymentError, match="Already"):
        services.reconcile(actor=other, payment=payment, statement_ref="L1")


def test_only_successful_payments_reconcile(awaiting_payment, finance_user, make_staff):
    from apps.core.roles import Role

    payment = services.record_manual_payment(
        actor=finance_user,
        appointment=awaiting_payment,
        provider_txn_id="LOW2",
        amount_ugx=1,
        amount_differs=True,
    )
    with pytest.raises(services.PaymentError, match="Only successful"):
        services.reconcile(actor=make_staff(Role.FINANCE), payment=payment, statement_ref="L2")


def test_auto_refund_cannot_be_paid_out_by_the_recorder(awaiting_payment, finance_user, make_staff):
    from apps.core.roles import Role

    Appointment.objects.filter(pk=awaiting_payment.pk).update(
        payment_due_at=timezone.now() - timedelta(minutes=1)
    )
    lapse_unpaid_appointments()
    services.record_manual_payment(
        actor=finance_user, appointment=awaiting_payment, provider_txn_id="LATE2", amount_ugx=44_000
    )
    refund = Refund.objects.get()
    assert refund.requested_by == finance_user
    with pytest.raises(PermissionDenied):
        services.mark_refund_paid(actor=finance_user, refund=refund, payout_ref="X")
    services.mark_refund_paid(actor=make_staff(Role.FINANCE), refund=refund, payout_ref="X")


@pytest.mark.parametrize(("accept", "booked_after"), [(True, True), (False, False)])
def test_second_person_resolves_mismatch(
    awaiting_payment, finance_user, make_staff, accept, booked_after
):
    from apps.core.roles import Role

    payment = services.record_manual_payment(
        actor=finance_user,
        appointment=awaiting_payment,
        provider_txn_id="ODD1",
        amount_ugx=40_000,
        amount_differs=True,
    )
    with pytest.raises(PermissionDenied, match="Someone else"):
        services.resolve_mismatch(actor=finance_user, payment=payment, accept=accept, note="x")
    other = make_staff(Role.FINANCE)
    with pytest.raises(services.PaymentError, match="Say why"):
        services.resolve_mismatch(actor=other, payment=payment, accept=accept, note=" ")
    services.resolve_mismatch(
        actor=other, payment=payment, accept=accept, note="Agent fee deducted"
    )
    awaiting_payment.refresh_from_db()
    assert (awaiting_payment.status == Appointment.Status.BOOKED) is booked_after
    if not accept:
        assert Refund.objects.get().amount_ugx == 40_000  # refunds what was actually received
    with pytest.raises(services.PaymentError, match="no amount mismatch"):
        services.resolve_mismatch(actor=other, payment=payment, accept=accept, note="again")


@DEV
def test_same_transaction_under_new_event_id_is_not_a_500(
    pending_payment, customer, make_customer, published_property, coordinator, make_broker
):
    body, meta = signed(event(pending_payment))
    services.handle_webhook(body=body, meta=meta)
    from apps.bookings import services as bookings
    from conftest import in_hours

    other = bookings.request_viewing(
        customer=make_customer(), prop=published_property, preferred_start=in_hours(30)
    )
    bookings.confirm(
        actor=coordinator, appointment=other, scheduled_start=in_hours(30), broker=make_broker("B2")
    )
    second = Payment.objects.create(appointment=other, provider="dev", amount_ugx=44_000)
    body, meta = signed(event(second, id="evt_other"))  # same transaction_id "MTN123"
    assert services.handle_webhook(body=body, meta=meta) == "duplicate_txn"
    assert WebhookEvent.objects.get(event_id="evt_other").outcome == "duplicate_txn"
