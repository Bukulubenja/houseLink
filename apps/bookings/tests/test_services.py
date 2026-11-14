from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.test import override_settings
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.bookings import services
from apps.bookings.models import Appointment, ViewingReport
from apps.listings.models import Property
from apps.payments.models import Refund
from conftest import in_hours

pytestmark = pytest.mark.django_db(transaction=True)  # on_commit SMS must fire
S = Appointment.Status


# --- Request -------------------------------------------------------------------


def test_request_creates_pending_request_and_texts_customer(requested, customer, sms_outbox):
    assert requested.status == S.REQUESTED
    assert requested.reference.startswith("CL") and len(requested.reference) == 8
    assert requested.fee_ugx is None  # nothing to pay yet
    assert any("Nothing to pay yet" in m.body for m in sms_outbox if m.to == customer.phone)


def test_cannot_request_unpublished_home(customer, draft_property):
    with pytest.raises(services.BookingError, match="no longer available"):
        services.request_viewing(
            customer=customer, prop=draft_property, preferred_start=in_hours(26)
        )


@pytest.mark.parametrize("hours", [1, 24 * 30])
def test_request_time_must_be_in_window(customer, published_property, hours):
    with pytest.raises(services.BookingError):
        services.request_viewing(
            customer=customer, prop=published_property, preferred_start=in_hours(hours)
        )


def test_one_open_request_per_home(requested, customer, published_property):
    with pytest.raises(services.BookingError, match="already have an open viewing"):
        services.request_viewing(
            customer=customer, prop=published_property, preferred_start=in_hours(30)
        )


def test_open_request_cap(customer, published_property):
    with override_settings(MAX_OPEN_REQUESTS_PER_CUSTOMER=1):
        services.request_viewing(
            customer=customer, prop=published_property, preferred_start=in_hours(26)
        )
        other = Property.objects.get(pk=published_property.pk)
        other.pk = None
        other.id = None
        other.save()
        with pytest.raises(services.BookingError, match="up to 1 open"):
            services.request_viewing(customer=customer, prop=other, preferred_start=in_hours(26))


def test_daily_request_rate_limit(customer, published_property):
    with override_settings(VIEWING_REQUESTS_PER_DAY=0), pytest.raises(services.BookingError):
        services.request_viewing(
            customer=customer, prop=published_property, preferred_start=in_hours(26)
        )


# --- Confirm / decline ---------------------------------------------------------


def test_confirm_fixes_fee_and_payment_deadline(awaiting_payment, customer, sms_outbox):
    assert awaiting_payment.status == S.AWAITING_PAYMENT
    assert awaiting_payment.fee_ugx == 44_000
    assert awaiting_payment.payment_due_at <= in_hours(12)
    assert awaiting_payment.payment_due_at <= awaiting_payment.scheduled_start - timedelta(hours=2)
    texts = [m.body for m in sms_outbox if m.to == customer.phone]
    assert any("UGX 44,000" in body and "0772000000" in body for body in texts)


def test_payment_deadline_tightens_for_soon_viewings(requested, coordinator, broker):
    appointment = services.confirm(
        actor=coordinator, appointment=requested, scheduled_start=in_hours(5), broker=broker
    )
    assert abs(appointment.payment_due_at - in_hours(3)) < timedelta(minutes=1)


def test_only_coordinators_confirm(requested, approver, broker):
    with pytest.raises(PermissionDenied):
        services.confirm(
            actor=approver, appointment=requested, scheduled_start=in_hours(26), broker=broker
        )


def test_broker_cannot_be_double_booked(
    awaiting_payment, coordinator, broker, make_customer, published_property
):
    other = services.request_viewing(
        customer=make_customer(), prop=published_property, preferred_start=in_hours(26)
    )
    with pytest.raises(services.BookingError, match="already has a viewing"):
        services.confirm(
            actor=coordinator,
            appointment=other,
            scheduled_start=awaiting_payment.scheduled_start + timedelta(minutes=30),
            broker=broker,
        )
    services.confirm(
        actor=coordinator,
        appointment=other,
        scheduled_start=awaiting_payment.scheduled_start + timedelta(minutes=60),
        broker=broker,
    )


def test_suspended_broker_cannot_be_assigned(requested, coordinator, broker):
    from apps.brokers.services import set_broker_status

    set_broker_status(actor=coordinator, broker=broker, status="suspended", reason="Late twice")
    with pytest.raises(services.BookingError, match="not active"):
        services.confirm(
            actor=coordinator, appointment=requested, scheduled_start=in_hours(26), broker=broker
        )


def test_cannot_confirm_after_listing_comes_down(requested, coordinator, broker, acquirer):
    from apps.listings.services import change_availability

    change_availability(actor=acquirer, prop=requested.property, action="rented", reason="Let")
    with pytest.raises(services.BookingError, match="no longer published"):
        services.confirm(
            actor=coordinator, appointment=requested, scheduled_start=in_hours(26), broker=broker
        )


def test_cannot_confirm_twice(awaiting_payment, coordinator, broker):
    with pytest.raises(services.BookingError, match="Only new requests"):
        services.confirm(
            actor=coordinator,
            appointment=awaiting_payment,
            scheduled_start=in_hours(30),
            broker=broker,
        )


def test_decline_needs_staff_reason(requested, coordinator, customer, sms_outbox):
    with pytest.raises(services.BookingError):
        services.decline(actor=coordinator, appointment=requested, reason="customer")
    services.decline(actor=coordinator, appointment=requested, reason="access_denied")
    requested.refresh_from_db()
    assert requested.status == S.DECLINED
    assert any("not been charged" in m.body for m in sms_outbox if m.to == customer.phone)
    with pytest.raises(services.BookingError):
        services.decline(actor=coordinator, appointment=requested, reason="other")


# --- Paid booking --------------------------------------------------------------


def test_payment_books_and_tells_broker(booked, broker, sms_outbox):
    assert booked.status == S.BOOKED
    assert booked.paid_at is not None
    assert any("assigned" in m.body for m in sms_outbox if m.to == broker.user.phone)


def test_customer_cancel_before_cutoff_is_refunded(booked, customer):
    services.cancel_by_customer(customer=customer, appointment=booked)
    booked.refresh_from_db()
    assert booked.status == S.CANCELLED
    refund = Refund.objects.get(payment__appointment=booked)
    assert refund.amount_ugx == 44_000
    assert refund.requested_by is None


def test_customer_cancel_after_cutoff_is_not_refunded(booked, customer):
    Appointment.objects.filter(pk=booked.pk).update(scheduled_start=in_hours(2))
    booked.refresh_from_db()
    assert not services.refund_due_on_customer_cancel(booked)
    services.cancel_by_customer(customer=customer, appointment=booked)
    assert not Refund.objects.exists()


def test_customer_cancels_unpaid_for_free(awaiting_payment, customer):
    services.cancel_by_customer(customer=customer, appointment=awaiting_payment)
    awaiting_payment.refresh_from_db()
    assert awaiting_payment.status == S.CANCELLED
    with pytest.raises(services.BookingError):
        services.cancel_by_customer(customer=customer, appointment=awaiting_payment)


def test_customer_cannot_cancel_someone_elses(booked, make_customer):
    with pytest.raises(PermissionDenied):
        services.cancel_by_customer(customer=make_customer(), appointment=booked)


def test_staff_cancel_of_paid_booking_refunds(booked, coordinator, broker, sms_outbox):
    services.cancel_by_staff(actor=coordinator, appointment=booked, reason="broker_no_show")
    refund = Refund.objects.get()
    assert refund.requested_by == coordinator
    assert refund.reason == "Broker did not attend"
    assert any("cancelled" in m.body for m in sms_outbox if m.to == broker.user.phone)
    with pytest.raises(services.BookingError):
        services.cancel_by_staff(actor=coordinator, appointment=booked, reason="other")


def test_staff_cancel_unknown_reason(booked, coordinator):
    with pytest.raises(services.BookingError):
        services.cancel_by_staff(actor=coordinator, appointment=booked, reason="nope")


def test_reschedule_moves_slot_and_broker(booked, coordinator, make_broker, sms_outbox):
    new_broker = make_broker("Peter Broker")
    services.reschedule(
        actor=coordinator, appointment=booked, scheduled_start=in_hours(40), broker=new_broker
    )
    booked.refresh_from_db()
    assert booked.broker == new_broker
    assert any("reassigned" in m.body for m in sms_outbox)
    assert AuditEvent.objects.filter(action="appointment.rescheduled").exists()


def test_reschedule_awaiting_payment_resets_deadline(awaiting_payment, coordinator, broker):
    services.reschedule(
        actor=coordinator, appointment=awaiting_payment, scheduled_start=in_hours(5), broker=broker
    )
    awaiting_payment.refresh_from_db()
    assert awaiting_payment.payment_due_at <= in_hours(3)


def test_cannot_reschedule_request(requested, coordinator, broker):
    with pytest.raises(services.BookingError):
        services.reschedule(
            actor=coordinator, appointment=requested, scheduled_start=in_hours(30), broker=broker
        )


# --- Broker on the day -------------------------------------------------------------


def _start_soon(appointment):
    Appointment.objects.filter(pk=appointment.pk).update(scheduled_start=in_hours(0.25))
    appointment.refresh_from_db()


def test_check_in_only_by_assigned_broker_near_start(booked, broker, make_broker, customer):
    with pytest.raises(services.BookingError, match="when you arrive"):
        services.check_in(user=broker.user, appointment=booked)
    _start_soon(booked)
    with pytest.raises(PermissionDenied):
        services.check_in(user=make_broker("Other").user, appointment=booked)
    with pytest.raises(PermissionDenied):
        services.check_in(user=customer, appointment=booked)
    services.check_in(user=broker.user, appointment=booked)
    booked.refresh_from_db()
    assert booked.checked_in_at is not None


def test_check_in_needs_paid_booking(awaiting_payment, broker):
    with pytest.raises(services.BookingError):
        services.check_in(user=broker.user, appointment=awaiting_payment)


def _report(appointment, broker, **overrides):
    values = {
        "customer_attended": True,
        "property_as_listed": True,
        "customer_interest": "wants_to_rent",
        "notes": "Liked the kitchen",
    }
    values.update(overrides)
    return services.submit_report(user=broker.user, appointment=appointment, **values)


def test_report_requires_check_in(booked, broker):
    with pytest.raises(services.BookingError, match="Check in"):
        _report(booked, broker)


def test_report_completes_viewing(booked, broker, customer, sms_outbox):
    _start_soon(booked)
    services.check_in(user=broker.user, appointment=booked)
    _report(booked, broker)
    booked.refresh_from_db()
    assert booked.status == S.COMPLETED
    assert any("thanks for viewing" in m.body for m in sms_outbox if m.to == customer.phone)
    with pytest.raises(services.BookingError):
        _report(booked, broker)


def test_no_show_report(booked, broker):
    _start_soon(booked)
    services.check_in(user=broker.user, appointment=booked)
    with pytest.raises(services.BookingError, match="Wait until"):
        _report(booked, broker, customer_attended=False, customer_interest="undecided")
    Appointment.objects.filter(pk=booked.pk).update(scheduled_start=in_hours(-0.5))
    report = _report(booked, broker, customer_attended=False, customer_interest="undecided")
    booked.refresh_from_db()
    assert booked.status == S.CUSTOMER_NO_SHOW
    assert report.customer_interest == ViewingReport.Interest.NOT_APPLICABLE


def test_mismatch_report_flags_listing(booked, broker):
    _start_soon(booked)
    services.check_in(user=broker.user, appointment=booked)
    with pytest.raises(services.BookingError, match="didn't match"):
        _report(booked, broker, property_as_listed=False)
    with pytest.raises(services.BookingError, match="interest"):
        _report(booked, broker, customer_interest="maybe")
    _report(booked, broker, property_as_listed=False, discrepancy_note="Only one bedroom")
    assert AuditEvent.objects.filter(action="property.flagged").exists()


def test_feedback_after_completion_only(booked, broker, customer, make_customer):
    with pytest.raises(services.BookingError, match="after the viewing"):
        services.leave_feedback(
            customer=customer, appointment=booked, rating=5, comment="", may_publish=False
        )
    _start_soon(booked)
    services.check_in(user=broker.user, appointment=booked)
    _report(booked, broker)
    booked.refresh_from_db()
    with pytest.raises(PermissionDenied):
        services.leave_feedback(
            customer=make_customer(), appointment=booked, rating=5, comment="", may_publish=False
        )
    with pytest.raises(services.BookingError, match="1 to 5"):
        services.leave_feedback(
            customer=customer, appointment=booked, rating=9, comment="", may_publish=False
        )
    services.leave_feedback(
        customer=customer, appointment=booked, rating=5, comment="Great", may_publish=True
    )
    with pytest.raises(services.BookingError, match="already"):
        services.leave_feedback(
            customer=customer, appointment=booked, rating=4, comment="", may_publish=False
        )


# --- Scheduled jobs -------------------------------------------------------------


def test_unpaid_slots_lapse(awaiting_payment, customer, sms_outbox):
    assert services.lapse_unpaid_appointments() == 0
    Appointment.objects.filter(pk=awaiting_payment.pk).update(
        payment_due_at=timezone.now() - timedelta(minutes=1)
    )
    assert services.lapse_unpaid_appointments() == 1
    awaiting_payment.refresh_from_db()
    assert awaiting_payment.status == S.LAPSED
    assert any("released" in m.body for m in sms_outbox if m.to == customer.phone)


def test_reminders_sent_once(booked, customer, broker, sms_outbox):
    from apps.bookings.tasks import lapse_unpaid_appointments, send_reminders

    assert send_reminders.delay().get() == 0
    Appointment.objects.filter(pk=booked.pk).update(scheduled_start=in_hours(1))
    sms_outbox.clear()
    assert send_reminders.delay().get() == 1
    assert {m.to for m in sms_outbox} == {customer.phone, broker.user.phone}
    assert services.send_reminders() == 0
    assert lapse_unpaid_appointments.delay().get() == 0


def test_customer_sms_never_shows_broker_phone(
    make_broker, requested, coordinator, finance_user, customer, sms_outbox
):
    from apps.accounts.models import User
    from apps.payments.services import record_manual_payment

    nameless = make_broker("Temp")
    User.objects.filter(pk=nameless.user_id).update(full_name="")
    nameless.user.refresh_from_db()
    confirmed = services.confirm(
        actor=coordinator, appointment=requested, scheduled_start=in_hours(26), broker=nameless
    )
    record_manual_payment(
        actor=finance_user, appointment=confirmed, provider_txn_id="N1", amount_ugx=44_000
    )
    booked_text = [m.body for m in sms_outbox if m.to == customer.phone][-1]
    assert nameless.user.phone not in booked_text
    assert "a Crib Link broker" in booked_text
