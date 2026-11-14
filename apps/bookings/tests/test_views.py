import pytest
from django.urls import reverse
from django.utils import timezone

from apps.bookings.models import Appointment
from conftest import in_hours

pytestmark = pytest.mark.django_db
S = Appointment.Status


def _local(dt) -> str:
    return timezone.localtime(dt).strftime("%Y-%m-%dT%H:%M")


def test_request_needs_sign_in(client, published_property):
    response = client.get(reverse("bookings:request", args=[published_property.pk]))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_request_flow(client, customer, published_property):
    client.force_login(customer)
    url = reverse("bookings:request", args=[published_property.pk])
    page = client.get(url)
    assert b"Nothing to pay yet" in page.content
    assert b"44,000" in page.content

    response = client.post(url, {"preferred_start": _local(in_hours(30)), "note": "After work"})
    appointment = Appointment.objects.get()
    assert response.status_code == 302
    assert response.url == reverse("bookings:detail", args=[appointment.pk])
    detail = client.get(response.url)
    assert b"request, not a confirmed booking" in detail.content

    again = client.post(url, {"preferred_start": _local(in_hours(30))})
    assert b"already have an open viewing" in again.content


def test_request_for_unpublished_home_404(client, customer, draft_property):
    client.force_login(customer)
    assert client.get(reverse("bookings:request", args=[draft_property.pk])).status_code == 404


def test_customers_only_see_their_own(client, requested, make_customer):
    client.force_login(make_customer())
    assert client.get(reverse("bookings:detail", args=[requested.pk])).status_code == 404
    assert client.post(reverse("bookings:cancel", args=[requested.pk])).status_code == 404
    assert requested.reference.encode() not in client.get(reverse("bookings:list")).content


def test_awaiting_payment_page_shows_instructions(client, customer, awaiting_payment):
    client.force_login(customer)
    response = client.get(reverse("bookings:detail", args=[awaiting_payment.pk]))
    assert b"0772000000" in response.content
    assert awaiting_payment.reference.encode() in response.content
    pay = client.post(reverse("bookings:pay", args=[awaiting_payment.pk]))
    assert pay.status_code == 302  # manual provider: back to the page with instructions


def test_pay_when_not_due_shows_error(client, customer, requested):
    client.force_login(customer)
    response = client.post(reverse("bookings:pay", args=[requested.pk]), follow=True)
    assert b"isn&#x27;t waiting for payment" in response.content


def test_cancel_and_feedback_views(client, customer, booked, broker):
    from apps.bookings import services

    client.force_login(customer)
    Appointment.objects.filter(pk=booked.pk).update(scheduled_start=in_hours(0.25))
    services.check_in(user=broker.user, appointment=booked)
    services.submit_report(
        user=broker.user,
        appointment=booked,
        customer_attended=True,
        property_as_listed=True,
        customer_interest="undecided",
    )
    detail = reverse("bookings:detail", args=[booked.pk])
    assert b"How did it go?" in client.get(detail).content
    bad = client.post(reverse("bookings:feedback", args=[booked.pk]), {"rating": "9"}, follow=True)
    assert b"Choose a rating" in bad.content
    client.post(reverse("bookings:feedback", args=[booked.pk]), {"rating": "5", "comment": "Great"})
    assert booked.feedback.rating == 5
    twice = client.post(
        reverse("bookings:feedback", args=[booked.pk]), {"rating": "4"}, follow=True
    )
    assert b"already" in twice.content
    closed = client.post(reverse("bookings:cancel", args=[booked.pk]), follow=True)
    assert b"can no longer be cancelled" in closed.content


def test_customer_cancel_view(client, customer, requested):
    client.force_login(customer)
    client.post(reverse("bookings:cancel", args=[requested.pk]))
    requested.refresh_from_db()
    assert requested.status == S.CANCELLED


# --- Broker ---------------------------------------------------------------------


def test_broker_pages_hidden_from_others(client, customer, booked):
    client.force_login(customer)
    assert client.get(reverse("broker:home")).status_code == 404
    assert client.get(reverse("broker:appointment", args=[booked.pk])).status_code == 404


def test_broker_sees_customer_only_after_payment(client, awaiting_payment, broker, finance_user):
    from apps.payments.services import record_manual_payment

    client.force_login(broker.user)
    home = client.get(reverse("broker:home"))
    assert b"details appear once paid" in home.content
    assert awaiting_payment.customer.phone.encode() not in home.content
    assert client.get(reverse("broker:appointment", args=[awaiting_payment.pk])).status_code == 404

    record_manual_payment(
        actor=finance_user, appointment=awaiting_payment, provider_txn_id="T1", amount_ugx=44_000
    )
    page = client.get(reverse("broker:appointment", args=[awaiting_payment.pk]))
    assert awaiting_payment.customer.phone.encode() in page.content
    assert b"Blue gate" in page.content


def test_broker_check_in_and_report_views(client, booked, broker):
    client.force_login(broker.user)
    url = reverse("broker:appointment", args=[booked.pk])
    early = client.post(url, {"action": "check_in"})
    assert b"when you arrive" in early.content

    Appointment.objects.filter(pk=booked.pk).update(scheduled_start=in_hours(0.25))
    client.post(url, {"action": "check_in"})
    assert b"Viewing report" in client.get(url).content
    response = client.post(
        url,
        {
            "action": "report",
            "customer_attended": "yes",
            "property_as_listed": "yes",
            "customer_interest": "wants_to_rent",
        },
    )
    assert response.status_code == 302
    booked.refresh_from_db()
    assert booked.status == S.COMPLETED


def test_other_broker_cannot_open_assignment(client, booked, make_broker):
    client.force_login(make_broker("Other").user)
    assert client.get(reverse("broker:appointment", args=[booked.pk])).status_code == 404


def test_broker_loses_access_after_report(client, booked, broker):
    from apps.bookings import services

    Appointment.objects.filter(pk=booked.pk).update(scheduled_start=in_hours(0.25))
    services.check_in(user=broker.user, appointment=booked)
    services.submit_report(
        user=broker.user,
        appointment=booked,
        customer_attended=True,
        property_as_listed=True,
        customer_interest="undecided",
    )
    client.force_login(broker.user)
    assert client.get(reverse("broker:appointment", args=[booked.pk])).status_code == 404
