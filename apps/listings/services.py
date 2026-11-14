"""Property intake, verification and approval (maker-checker), availability and expiry.

Rules from the operating plan:
- Nothing is published before an on-site verification against the full checklist.
- The approver must be neither the person who sourced the listing nor the one who verified it.
- A published listing carries its last-verified date and expires at its recheck date.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record
from apps.core.crypto import sha256_hex
from apps.core.phone import normalize_phone
from apps.core.roles import Role, has_role, require_role

from .models import CHECKLIST, Property, PropertyContact, PropertyPhoto, Verification

logger = logging.getLogger(__name__)

Status = Property.Status
Decision = Verification.Decision

EDITABLE_STATUSES = frozenset({Status.DRAFT, Status.EXPIRED, Status.PAUSED})
INSPECTABLE_STATUSES = frozenset({Status.DRAFT, Status.EXPIRED, Status.PUBLISHED, Status.PAUSED})
# Approving an inspection puts these back on the site. A listing paused or marked rented while
# its recheck waited keeps that status (only its verification dates are refreshed).
PUBLISH_ON_APPROVAL = frozenset({Status.AWAITING_APPROVAL, Status.PUBLISHED, Status.EXPIRED})


class ListingError(Exception):
    """User-facing failure; str(error) is safe to display."""


@dataclass(frozen=True)
class PropertyDetails:
    title: str
    area: str
    location_hint: str
    directions: str
    kind: str
    rent_ugx: int
    bedrooms: int
    bathrooms: int
    furnished: bool
    description: str


def _check_area(area: str) -> None:
    if area not in settings.SERVICE_AREAS:
        raise ListingError("That area is outside the current service area.")


# --- Intake (acquisition) ----------------------------------------------------


def create_contact(
    *, actor: User, name: str, phone: str, relationship: str, landlord_profile=None
) -> PropertyContact:
    require_role(actor, Role.ACQUISITION)
    with transaction.atomic():
        contact = PropertyContact.objects.create(
            name=name,
            phone=normalize_phone(phone),
            relationship=relationship,
            landlord_profile=landlord_profile,
            created_by=actor,
        )
        record(actor=actor, action="contact.created", target=contact)
    return contact


def create_property(
    *,
    actor: User,
    contact: PropertyContact,
    details: PropertyDetails,
    authority_document: ContentFile | None = None,
) -> Property:
    require_role(actor, Role.ACQUISITION)
    _check_area(details.area)
    with transaction.atomic():
        prop = Property(contact=contact, sourced_by=actor, **details.__dict__)
        if authority_document is not None:
            prop.authority_sha256 = sha256_hex(authority_document.read())
            authority_document.seek(0)
        prop.save()
        if authority_document is not None:
            prop.authority_document.save("authority.jpg", authority_document, save=True)
        record(
            actor=actor,
            action="property.created",
            target=prop,
            evidence={"contact": str(contact.pk), "authority_sha256": prop.authority_sha256},
        )
    return prop


def update_property(*, actor: User, prop: Property, details: PropertyDetails) -> Property:
    """Edits are only allowed off-air; a published listing must be paused first."""
    require_role(actor, Role.ACQUISITION)
    _check_area(details.area)
    with transaction.atomic():
        prop = Property.objects.select_for_update().get(pk=prop.pk)
        if prop.status not in EDITABLE_STATUSES:
            raise ListingError("Pause the listing before editing it.")
        if prop.verifications.filter(decision=Decision.PENDING).exists():
            raise ListingError("An inspection is awaiting approval; decide it before editing.")
        changed = {
            name: value for name, value in details.__dict__.items() if getattr(prop, name) != value
        }
        for name, value in changed.items():
            setattr(prop, name, value)
        if changed and prop.status != Status.DRAFT:
            prop.status = Status.DRAFT  # changed details must be inspected again
        prop.save()
        record(
            actor=actor, action="property.edited", target=prop, evidence={"fields": sorted(changed)}
        )
    return prop


def add_photo(
    *, actor: User, prop: Property, image: ContentFile, caption: str = ""
) -> PropertyPhoto:
    """`image` must already be sanitised (core.images.sanitize_image)."""
    if not _can_handle_media(actor):
        raise PermissionDenied("Only acquisition or verification staff add photos.")
    with transaction.atomic():
        prop = Property.objects.select_for_update().get(pk=prop.pk)
        # Public photos are part of what was inspected: change them only off-air.
        if (
            prop.status not in EDITABLE_STATUSES
            or prop.verifications.filter(decision=Decision.PENDING).exists()
        ):
            raise ListingError("Pause the listing (and decide any pending inspection) first.")
        if prop.photos.count() >= settings.LISTING_MAX_PHOTOS:
            raise ListingError(f"A listing can have at most {settings.LISTING_MAX_PHOTOS} photos.")
        photo = PropertyPhoto(
            property=prop, caption=caption, added_by=actor, position=prop.photos.count()
        )
        photo.image.save("photo.jpg", image, save=True)
        record(
            actor=actor,
            action="property.photo_added",
            target=prop,
            evidence={"photo": str(photo.pk)},
        )
    return photo


def _can_handle_media(actor: User) -> bool:
    return has_role(actor, Role.ACQUISITION) or has_role(actor, Role.VERIFICATION)


# --- Verification and approval ---------------------------------------------


def submit_verification(
    *,
    actor: User,
    prop: Property,
    checklist: dict[str, bool],
    checked_at: datetime,
    notes: str = "",
) -> Verification:
    require_role(actor, Role.VERIFICATION)
    if set(checklist) != set(CHECKLIST) or not all(checklist.values()):
        raise ListingError("Every checklist item must be confirmed on site before submitting.")
    if checked_at > timezone.now():
        raise ListingError("The inspection time can't be in the future.")
    if checked_at < timezone.now() - timedelta(days=settings.LISTING_RECHECK_DAYS):
        raise ListingError("This inspection is too old; inspect the property again.")

    with transaction.atomic():
        prop = Property.objects.select_for_update().get(pk=prop.pk)
        if (
            prop.status not in INSPECTABLE_STATUSES
            or prop.verifications.filter(decision=Decision.PENDING).exists()
        ):
            raise ListingError("This listing can't take a new inspection right now.")
        if not prop.photos.exists():
            raise ListingError("Add photos before submitting the inspection.")
        verification = Verification.objects.create(
            property=prop,
            verified_by=actor,
            checked_at=checked_at,
            checklist=dict.fromkeys(CHECKLIST, True),
            notes=notes,
        )
        previous = prop.status
        if prop.status != Status.PUBLISHED:
            # A published listing stays live while its recheck is reviewed.
            prop.status = Status.AWAITING_APPROVAL
            prop.save(update_fields=["status", "updated_at"])
        record(
            actor=actor,
            action="verification.submitted",
            target=verification,
            evidence={"property": str(prop.pk), "previous_status": previous},
        )
    return verification


def _require_independent_approver(actor: User, verification: Verification) -> None:
    require_role(actor, Role.APPROVAL)
    if actor.pk in {verification.verified_by_id, verification.property.sourced_by_id}:
        raise PermissionDenied("The approver must not have sourced or verified this listing.")


def decide_verification(
    *, actor: User, verification: Verification, approve: bool, note: str = ""
) -> Verification:
    with transaction.atomic():
        verification = (
            Verification.objects.select_for_update()
            .select_related("property")
            .get(pk=verification.pk)
        )
        _require_independent_approver(actor, verification)
        if verification.decision != Decision.PENDING:
            raise ListingError("This inspection has already been decided.")
        if not approve and not note.strip():
            raise ListingError("Say what needs fixing when sending a listing back.")

        prop = Property.objects.select_for_update().get(pk=verification.property_id)
        now = timezone.now()
        verification.decision = Decision.APPROVED if approve else Decision.REJECTED
        verification.decided_by = actor
        verification.decided_at = now
        verification.decision_note = note
        verification.save()

        if approve:
            prop.last_verified_at = verification.checked_at
            prop.recheck_due_at = verification.checked_at + timedelta(
                days=settings.LISTING_RECHECK_DAYS
            )
            if prop.recheck_due_at <= now:
                raise ListingError("This inspection is already past its recheck date.")
            if prop.status in PUBLISH_ON_APPROVAL:
                prop.status = Status.PUBLISHED
                prop.published_at = prop.published_at or now
        elif prop.status == Status.AWAITING_APPROVAL:
            prop.status = Status.DRAFT
        prop.save()
        record(
            actor=actor,
            action=f"verification.{verification.decision}",
            target=verification,
            evidence={"property": str(prop.pk), "note": note},
        )
    return verification


# --- Availability ------------------------------------------------------------

AVAILABILITY_ACTIONS = {
    "pause": (Status.PAUSED, {Status.PUBLISHED}),
    "rented": (Status.RENTED, {Status.PUBLISHED, Status.PAUSED, Status.EXPIRED}),
}


def change_availability(*, actor: User, prop: Property, action: str, reason: str) -> Property:
    """Pause or mark rented. Any staff on the listings side may take a listing down at once."""
    if not (
        _can_handle_media(actor) or has_role(actor, Role.APPROVAL) or has_role(actor, Role.BOOKINGS)
    ):
        raise PermissionDenied("Only operations staff change availability.")
    if action not in AVAILABILITY_ACTIONS:
        raise ListingError("Unknown action.")
    if not reason.strip():
        raise ListingError("Give a reason.")
    new_status, allowed_from = AVAILABILITY_ACTIONS[action]
    with transaction.atomic():
        prop = Property.objects.select_for_update().get(pk=prop.pk)
        if prop.status not in allowed_from:
            raise ListingError("That change isn't possible from the listing's current status.")
        previous = prop.status
        prop.status = new_status
        prop.save(update_fields=["status", "updated_at"])
        record(
            actor=actor,
            action=f"property.{new_status}",
            target=prop,
            evidence={"previous_status": previous, "reason": reason},
        )
    return prop


def expire_overdue_listings() -> int:
    """Published listings past their recheck date come off the site until re-inspected."""
    now = timezone.now()
    expired = 0
    due = Property.objects.filter(status=Status.PUBLISHED, recheck_due_at__lte=now)
    for prop in due.iterator():
        with transaction.atomic():
            locked = Property.objects.select_for_update().get(pk=prop.pk)
            if locked.status != Status.PUBLISHED or locked.recheck_due_at > now:
                continue
            locked.status = Status.EXPIRED
            locked.save(update_fields=["status", "updated_at"])
            record(actor=None, action="property.expired", target=locked)
        expired += 1
    if expired:
        logger.info("Expired %s listings past their recheck date", expired)
    return expired
