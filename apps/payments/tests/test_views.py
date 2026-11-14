import json
import time

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.bookings.models import Appointment
from apps.payments import providers
from apps.payments.models import Payment

pytestmark = pytest.mark.django_db
WEBHOOK = reverse("payments:webhook")


@override_settings(PAYMENT_PROVIDER="dev")
def test_dev_checkout_end_to_end(client, customer, awaiting_payment):
    client.force_login(customer)
    pay = client.post(reverse("bookings:pay", args=[awaiting_payment.pk]))
    payment = Payment.objects.get()
    assert pay.url == reverse("payments:dev_checkout", args=[payment.reference])
    assert b"Simulated checkout" in client.get(pay.url).content

    client.post(pay.url, {"result": "succeed"})
    awaiting_payment.refresh_from_db()
    assert awaiting_payment.status == Appointment.Status.BOOKED


@override_settings(PAYMENT_PROVIDER="dev")
def test_dev_checkout_failure_and_ownership(client, customer, make_customer, awaiting_payment):
    client.force_login(customer)
    client.post(reverse("bookings:pay", args=[awaiting_payment.pk]))
    payment = Payment.objects.get()
    client.post(reverse("payments:dev_checkout", args=[payment.reference]), {"result": "fail"})
    payment.refresh_from_db()
    assert payment.status == Payment.Status.FAILED

    client.force_login(make_customer())
    url = reverse("payments:dev_checkout", args=[payment.reference])
    assert client.get(url).status_code == 404


def test_dev_checkout_absent_unless_dev(client, customer, booked):
    client.force_login(customer)
    payment = Payment.objects.get()
    url = reverse("payments:dev_checkout", args=[payment.reference])
    assert client.get(url).status_code == 404


def test_webhook_rejects_unsigned(client):
    response = client.post(WEBHOOK, data=b"{}", content_type="application/json")
    assert response.status_code == 400


def test_webhook_rejects_get_and_oversize(client):
    assert client.get(WEBHOOK).status_code == 405
    big = b"x" * (64 * 1024 + 1)
    assert client.post(WEBHOOK, data=big, content_type="application/json").status_code == 413


@override_settings(PAYMENT_PROVIDER="dev")
def test_webhook_accepts_signed_event_without_csrf(customer, awaiting_payment):
    from django.test import Client

    from apps.payments import services

    services.start_checkout(customer=customer, appointment=awaiting_payment)
    payment = Payment.objects.get()
    body = json.dumps(
        {
            "id": "evt_9",
            "reference": payment.reference,
            "status": "succeeded",
            "amount": payment.amount_ugx,
            "currency": "UGX",
            "transaction_id": "AIR9",
        }
    ).encode()
    ts = int(time.time())
    signature = f"t={ts},v1={providers.sign('test-webhook-secret', ts, body)}"
    response = Client(enforce_csrf_checks=True).post(
        WEBHOOK, data=body, content_type="application/json", HTTP_CRIB_SIGNATURE=signature
    )
    assert response.status_code == 200
    assert response.content == b"succeeded"
