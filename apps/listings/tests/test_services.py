from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.test import override_settings
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.core.roles import Role
from apps.listings import services
from apps.listings.models import CHECKLIST, Property, Verification

pytestmark = pytest.mark.django_db
Status = Property.Status


def test_new_listing_is_a_draft_with_hashed_authority(draft_property):
    assert draft_property.status == Status.DRAFT
    assert len(draft_property.authority_sha256) == 64
    assert draft_property.authority_document.storage.exists(draft_property.authority_document.name)
    assert not Property.objects.bookable().exists()


def test_only_acquisition_staff_create_listings(verifier, property_contact, details):
    with pytest.raises(PermissionDenied):
        services.create_property(actor=verifier, contact=property_contact, details=details())


def test_area_outside_service_area_is_refused(acquirer, property_contact, details):
    with pytest.raises(services.ListingError, match="service area"):
        services.create_property(
            actor=acquirer, contact=property_contact, details=details(area="Entebbe")
        )


def test_photo_limit(acquirer, draft_property, jpeg):
    with override_settings(LISTING_MAX_PHOTOS=1), pytest.raises(services.ListingError):
        services.add_photo(actor=acquirer, prop=draft_property, image=jpeg())


def test_customer_cannot_add_photos(customer, draft_property, jpeg):
    with pytest.raises(PermissionDenied):
        services.add_photo(actor=customer, prop=draft_property, image=jpeg())


def test_inspection_needs_every_checklist_item(draft_property, verifier):
    partial = dict.fromkeys(CHECKLIST, True) | {"authority": False}
    with pytest.raises(services.ListingError, match="Every checklist item"):
        services.submit_verification(
            actor=verifier, prop=draft_property, checklist=partial, checked_at=timezone.now()
        )


@pytest.mark.parametrize("offset", [timedelta(hours=1), timedelta(days=-30)])
def test_inspection_time_must_be_recent_and_not_future(draft_property, verifier, offset):
    with pytest.raises(services.ListingError):
        services.submit_verification(
            actor=verifier,
            prop=draft_property,
            checklist=dict.fromkeys(CHECKLIST, True),
            checked_at=timezone.now() + offset,
        )


def test_inspection_needs_photos(acquirer, property_contact, details, verifier, inspect):
    bare = services.create_property(actor=acquirer, contact=property_contact, details=details())
    with pytest.raises(services.ListingError, match="photos"):
        inspect(bare, verifier)


def test_only_verifiers_inspect(draft_property, acquirer, inspect):
    with pytest.raises(PermissionDenied):
        inspect(draft_property, acquirer)


def test_inspection_moves_draft_to_awaiting_approval(draft_property, verifier, inspect):
    inspect(draft_property, verifier)
    draft_property.refresh_from_db()
    assert draft_property.status == Status.AWAITING_APPROVAL
    with pytest.raises(services.ListingError):
        inspect(draft_property, verifier)  # one pending inspection at a time


def test_approval_publishes_with_recheck_date(published_property):
    verification = published_property.latest_approved_verification
    assert published_property.status == Status.PUBLISHED
    assert published_property.is_bookable
    assert published_property.last_verified_at == verification.checked_at
    assert published_property.recheck_due_at == verification.checked_at + timedelta(days=14)
    assert AuditEvent.objects.filter(action="verification.approved").exists()


def test_sourcer_cannot_approve_own_listing(make_staff, property_contact, details, jpeg, verifier):
    both = make_staff(Role.ACQUISITION, Role.APPROVAL)
    prop = services.create_property(actor=both, contact=property_contact, details=details())
    services.add_photo(actor=both, prop=prop, image=jpeg())
    verification = services.submit_verification(
        actor=verifier,
        prop=prop,
        checklist=dict.fromkeys(CHECKLIST, True),
        checked_at=timezone.now(),
    )
    with pytest.raises(PermissionDenied, match="must not have sourced or verified"):
        services.decide_verification(actor=both, verification=verification, approve=True)


def test_verifier_cannot_approve_own_inspection(make_staff, draft_property, inspect):
    both = make_staff(Role.VERIFICATION, Role.APPROVAL)
    verification = inspect(draft_property, both)
    with pytest.raises(PermissionDenied):
        services.decide_verification(actor=both, verification=verification, approve=True)


def test_superuser_is_still_bound_by_separation_of_duties(draft_property, inspect):
    from apps.accounts.models import User

    boss = User.objects.create_superuser("+256700000009", password="a-long-admin-password")
    verification = inspect(draft_property, boss)
    with pytest.raises(PermissionDenied):
        services.decide_verification(actor=boss, verification=verification, approve=True)


def test_send_back_needs_a_note_and_returns_to_draft(draft_property, verifier, approver, inspect):
    verification = inspect(draft_property, verifier)
    with pytest.raises(services.ListingError, match="what needs fixing"):
        services.decide_verification(actor=approver, verification=verification, approve=False)
    services.decide_verification(
        actor=approver, verification=verification, approve=False, note="Photos are blurry"
    )
    draft_property.refresh_from_db()
    assert draft_property.status == Status.DRAFT
    with pytest.raises(services.ListingError, match="already been decided"):
        services.decide_verification(actor=approver, verification=verification, approve=True)


def test_stale_inspection_cannot_be_approved(draft_property, verifier, approver, inspect):
    verification = inspect(draft_property, verifier)
    Verification.objects.filter(pk=verification.pk).update(
        checked_at=timezone.now() - timedelta(days=14, minutes=1)
    )
    with pytest.raises(services.ListingError, match="past its recheck"):
        services.decide_verification(actor=approver, verification=verification, approve=True)


def test_overdue_listings_expire(published_property):
    Property.objects.filter(pk=published_property.pk).update(
        recheck_due_at=timezone.now() - timedelta(minutes=1)
    )
    assert not Property.objects.bookable().exists()
    assert services.expire_overdue_listings() == 1
    published_property.refresh_from_db()
    assert published_property.status == Status.EXPIRED
    assert services.expire_overdue_listings() == 0


def test_expire_task_runs(published_property):
    from apps.listings.tasks import expire_overdue_listings

    assert expire_overdue_listings.delay().get() == 0


def test_recheck_keeps_listing_live_then_extends_it(
    published_property, verifier, approver, inspect
):
    old_due = published_property.recheck_due_at
    verification = inspect(published_property, verifier, hours_ago=0.1)
    published_property.refresh_from_db()
    assert published_property.status == Status.PUBLISHED
    services.decide_verification(actor=approver, verification=verification, approve=True)
    published_property.refresh_from_db()
    assert published_property.recheck_due_at > old_due


def test_rented_while_recheck_pending_is_not_republished(
    published_property, verifier, approver, inspect, acquirer
):
    verification = inspect(published_property, verifier, hours_ago=0.1)
    services.change_availability(
        actor=acquirer, prop=published_property, action="rented", reason="Signed lease"
    )
    services.decide_verification(actor=approver, verification=verification, approve=True)
    published_property.refresh_from_db()
    assert published_property.status == Status.RENTED


def test_pause_and_rented_need_reason_and_valid_state(published_property, acquirer, customer):
    with pytest.raises(services.ListingError, match="reason"):
        services.change_availability(
            actor=acquirer, prop=published_property, action="pause", reason=" "
        )
    with pytest.raises(services.ListingError, match="Unknown"):
        services.change_availability(
            actor=acquirer, prop=published_property, action="x", reason="r"
        )
    with pytest.raises(PermissionDenied):
        services.change_availability(
            actor=customer, prop=published_property, action="pause", reason="r"
        )
    services.change_availability(
        actor=acquirer, prop=published_property, action="pause", reason="Owner away"
    )
    with pytest.raises(services.ListingError, match="current status"):
        services.change_availability(
            actor=acquirer, prop=published_property, action="pause", reason="again"
        )


def test_edit_requires_pause_and_forces_reinspection(published_property, acquirer, details):
    with pytest.raises(services.ListingError, match="Pause"):
        services.update_property(actor=acquirer, prop=published_property, details=details())
    services.change_availability(
        actor=acquirer, prop=published_property, action="pause", reason="Edit"
    )
    services.update_property(
        actor=acquirer, prop=published_property, details=details(rent_ugx=950_000)
    )
    published_property.refresh_from_db()
    assert published_property.rent_ugx == 950_000
    assert published_property.status == Status.DRAFT


def test_edit_blocked_while_inspection_pending(
    published_property, acquirer, verifier, inspect, details
):
    inspect(published_property, verifier, hours_ago=0.1)
    services.change_availability(
        actor=acquirer, prop=published_property, action="pause", reason="x"
    )
    with pytest.raises(services.ListingError, match="awaiting approval"):
        services.update_property(actor=acquirer, prop=published_property, details=details())


def test_photos_cannot_change_on_a_live_listing(published_property, acquirer, jpeg):
    with pytest.raises(services.ListingError, match="Pause the listing"):
        services.add_photo(actor=acquirer, prop=published_property, image=jpeg())
    services.change_availability(
        actor=acquirer, prop=published_property, action="pause", reason="x"
    )
    services.add_photo(actor=acquirer, prop=published_property, image=jpeg())
