from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.audit.models import AuditEvent
from apps.bookings.models import Appointment
from apps.brokers.models import BrokerProfile
from apps.core.roles import Role
from apps.listings.models import CHECKLIST, Property, PropertyContact
from apps.payments.models import Payment, Refund
from conftest import in_hours

pytestmark = pytest.mark.django_db


def _upload(name="photo.png") -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", (30, 20), (10, 90, 60)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def _local(dt) -> str:
    return timezone.localtime(dt).strftime("%Y-%m-%dT%H:%M")


PROPERTY_FIELDS = {
    "title": "Bedsitter near Capital Shoppers",
    "area": "Ntinda",
    "location_hint": "Ntinda trading centre",
    "directions": "Behind the church, green gate",
    "kind": "bedsitter",
    "rent_ugx": "350000",
    "bedrooms": "1",
    "bathrooms": "1",
    "description": "",
}


def test_dashboard_for_any_role(client, finance_user, booked):
    client.force_login(finance_user)
    response = client.get(reverse("ops:dashboard"))
    assert response.status_code == 200
    assert b"Active verified properties" in response.content
    assert b"Confirmed" in response.content


def test_role_pages_404_for_wrong_role(client, finance_user):
    client.force_login(finance_user)
    assert client.get(reverse("ops:property_new")).status_code == 404
    assert client.get(reverse("ops:booking_queue")).status_code == 404


def test_full_listing_pipeline_through_views(client, acquirer, verifier, approver):
    client.force_login(acquirer)
    client.post(
        reverse("ops:contact_new"),
        {"name": "Sarah Owner", "phone": "0772999888", "relationship": "owner"},
    )
    contact = PropertyContact.objects.get()
    response = client.post(
        reverse("ops:property_new"),
        {**PROPERTY_FIELDS, "contact": contact.pk, "authority_document": _upload("auth.png")},
    )
    prop = Property.objects.get()
    assert response.url == reverse("ops:property_detail", args=[prop.pk])
    detail = reverse("ops:property_detail", args=[prop.pk])
    client.post(detail, {"form": "photo", "image": _upload(), "caption": "Front"})
    assert prop.photos.count() == 1

    client.force_login(verifier)
    checks = {f"check_{key}": "on" for key in CHECKLIST}
    client.post(detail, {"form": "verify", "checked_at": _local(in_hours(-1)), **checks})
    prop.refresh_from_db()
    assert prop.status == Property.Status.AWAITING_APPROVAL
    assert b"must approve it" in client.get(detail).content  # verifier can't decide

    authority = client.get(reverse("ops:authority_document", args=[prop.pk]))
    assert authority.status_code == 200
    assert "no-store" in authority["Cache-Control"]
    assert AuditEvent.objects.filter(action="property.authority_viewed").exists()

    client.force_login(approver)
    client.post(detail, {"form": "decide", "decision": "approve", "note": ""})
    prop.refresh_from_db()
    assert prop.status == Property.Status.PUBLISHED
    assert client.get(reverse("listings:detail", args=[prop.pk])).status_code == 200

    client.post(detail, {"form": "availability", "action": "pause", "reason": "Owner away"})
    prop.refresh_from_db()
    assert prop.status == Property.Status.PAUSED


def test_property_list_and_edit(client, acquirer, published_property):
    client.force_login(acquirer)
    listing = client.get(reverse("ops:property_list") + "?status=published")
    assert published_property.title.encode() in listing.content
    edit = reverse("ops:property_edit", args=[published_property.pk])
    refused = client.post(edit, {**PROPERTY_FIELDS})
    assert b"Pause the listing" in refused.content
    assert client.get(edit).status_code == 200


def test_verification_error_is_shown(client, verifier, acquirer, property_contact, details):
    from apps.listings.services import create_property

    prop = create_property(actor=acquirer, contact=property_contact, details=details())
    client.force_login(verifier)
    checks = {f"check_{key}": "on" for key in CHECKLIST}
    response = client.post(
        reverse("ops:property_detail", args=[prop.pk]),
        {"form": "verify", "checked_at": _local(in_hours(-1)), **checks},
    )
    assert b"Add photos" in response.content


def test_booking_desk_confirm_and_cancel(client, coordinator, requested, broker):
    client.force_login(coordinator)
    queue = client.get(reverse("ops:booking_queue"))
    assert requested.reference.encode() in queue.content
    url = reverse("ops:booking_detail", args=[requested.pk])
    assert requested.customer.phone.encode() in client.get(url).content
    client.post(
        url, {"form": "confirm", "scheduled_start": _local(in_hours(28)), "broker": broker.pk}
    )
    requested.refresh_from_db()
    assert requested.status == Appointment.Status.AWAITING_PAYMENT

    clash = client.post(
        url, {"form": "confirm", "scheduled_start": _local(in_hours(28)), "broker": broker.pk}
    )
    assert b"Only new requests" in clash.content

    client.post(
        url, {"form": "reschedule", "scheduled_start": _local(in_hours(30)), "broker": broker.pk}
    )
    client.post(url, {"form": "cancel", "reason": "access_denied", "note": "Caretaker away"})
    requested.refresh_from_db()
    assert requested.status == Appointment.Status.CANCELLED


def test_booking_desk_decline(client, coordinator, requested):
    client.force_login(coordinator)
    url = reverse("ops:booking_detail", args=[requested.pk])
    client.post(url, {"form": "decline", "reason": "property_unavailable"})
    requested.refresh_from_db()
    assert requested.status == Appointment.Status.DECLINED


def test_broker_onboarding_and_suspension_views(client, coordinator):
    client.force_login(coordinator)
    url = reverse("ops:broker_list")
    client.post(
        url,
        {
            "full_name": "Ivan Broker",
            "phone": "0755333444",
            "id_checked_at": _local(in_hours(-3)),
            "terms_signed_at": _local(in_hours(-2)),
            "areas": ["Ntinda", "Naguru"],
        },
    )
    broker = BrokerProfile.objects.get()
    assert broker.areas == ["Ntinda", "Naguru"]
    status_url = reverse("ops:broker_status", args=[broker.pk])
    client.post(status_url, {"status": "suspended"})  # no reason: refused
    broker.refresh_from_db()
    assert broker.status == "active"
    client.post(status_url, {"status": "suspended", "reason": "Late"})
    broker.refresh_from_db()
    assert broker.status == "suspended"


def test_finance_record_refund_reconcile(client, finance_user, make_staff, awaiting_payment):
    client.force_login(finance_user)
    assert awaiting_payment.reference.encode() in client.get(reverse("ops:finance")).content
    record = reverse("ops:record_payment")
    assert (
        b"No viewing"
        in client.post(
            record,
            {"reference": "CLNOPE00", "provider_txn_id": "T", "amount_ugx": "1"},
            follow=True,
        ).content
    )
    assert b"Enter the booking" in client.post(record, {}, follow=True).content
    typo = client.post(
        record,
        {"reference": awaiting_payment.reference, "provider_txn_id": "MM1", "amount_ugx": "4400"},
        follow=True,
    )
    assert b"amount differs" in typo.content
    client.post(
        record,
        {
            "reference": awaiting_payment.reference.lower(),
            "provider_txn_id": "MM1",
            "amount_ugx": "44000",
        },
    )
    awaiting_payment.refresh_from_db()
    assert awaiting_payment.status == Appointment.Status.BOOKED
    payment = Payment.objects.get()

    own = client.post(
        reverse("ops:reconcile_payment", args=[payment.pk]), {"statement_ref": "L1"}, follow=True
    )
    assert b"Someone else" in own.content

    coordinator = make_staff(Role.BOOKINGS)
    client.force_login(coordinator)
    client.post(
        reverse("ops:booking_detail", args=[awaiting_payment.pk]),
        {"form": "cancel", "reason": "property_unavailable"},
    )
    refund = Refund.objects.get()

    other_finance = make_staff(Role.FINANCE)
    client.force_login(other_finance)
    client.post(reverse("ops:reconcile_payment", args=[payment.pk]), {"statement_ref": "L1"})
    client.post(reverse("ops:pay_refund", args=[refund.pk]), {"payout_ref": "OUT1"})
    payment.refresh_from_db()
    refund.refresh_from_db()
    assert payment.reconciled_by == other_finance
    assert refund.status == Refund.Status.PAID
    assert (
        b"Enter the payout"
        in client.post(reverse("ops:pay_refund", args=[refund.pk]), {}, follow=True).content
    )
    assert (
        b"Enter the statement"
        in client.post(reverse("ops:reconcile_payment", args=[payment.pk]), {}, follow=True).content
    )


def test_finance_mismatch_held_and_settled(client, finance_user, make_staff, awaiting_payment):
    client.force_login(finance_user)
    held = client.post(
        reverse("ops:record_payment"),
        {
            "reference": awaiting_payment.reference,
            "provider_txn_id": "ODD9",
            "amount_ugx": "40000",
            "amount_differs": "on",
        },
        follow=True,
    )
    assert b"held for a second person" in held.content
    assert b"another finance person settles this" in held.content
    payment = Payment.objects.get()

    client.force_login(make_staff(Role.FINANCE))
    url = reverse("ops:resolve_payment", args=[payment.pk])
    assert b"Choose a decision" in client.post(url, {}, follow=True).content
    client.post(url, {"decision": "accept", "note": "Customer topped up in cash"})
    awaiting_payment.refresh_from_db()
    assert awaiting_payment.status == Appointment.Status.BOOKED
