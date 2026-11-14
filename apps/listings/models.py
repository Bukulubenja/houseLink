"""Staff-sourced rental listings.

Lifecycle: DRAFT -> (verifier inspects) AWAITING_APPROVAL -> (a different person approves)
PUBLISHED -> PAUSED / RENTED / EXPIRED. An expired listing needs a fresh inspection.
"""

import builtins
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import PublicModel
from apps.core.storage import private_storage


class PropertyContact(PublicModel):
    """The owner, caretaker or agent who authorised Crib Link to advertise a property."""

    class Relationship(models.TextChoices):
        OWNER = "owner", "Owner"
        CARETAKER = "caretaker", "Caretaker (authorised by the owner)"
        AGENT = "agent", "Agent (authorised by the owner)"

    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=16, help_text="E.164")
    relationship = models.CharField(max_length=16, choices=Relationship.choices)
    landlord_profile = models.ForeignKey(
        "landlords.LandlordProfile",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="property_contacts",
        help_text="Set when the owner has also passed the ID check.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )

    def __str__(self) -> str:
        return f"{self.name} ({self.get_relationship_display()})"


def authority_upload_to(instance: "Property", filename: str) -> str:
    return f"authority/{instance.pk}/{uuid.uuid4().hex}.jpg"


def photo_upload_to(instance: "PropertyPhoto", filename: str) -> str:
    return f"listings/{instance.property_id}/{uuid.uuid4().hex}.jpg"


class PropertyQuerySet(models.QuerySet):
    def bookable(self) -> "PropertyQuerySet":
        """Published and inside its recheck window: the only listings customers can see."""
        return self.filter(status=Property.Status.PUBLISHED, recheck_due_at__gt=timezone.now())


class Property(PublicModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        AWAITING_APPROVAL = "awaiting_approval", "Verified, awaiting approval"
        PUBLISHED = "published", "Published"
        PAUSED = "paused", "Paused"
        RENTED = "rented", "Rented"
        EXPIRED = "expired", "Expired (needs re-inspection)"

    class Kind(models.TextChoices):
        SINGLE_ROOM = "single_room", "Single room"
        DOUBLE_ROOM = "double_room", "Double room"
        APARTMENT = "apartment", "Apartment"
        HOUSE = "house", "House"
        BEDSITTER = "bedsitter", "Bedsitter"

    title = models.CharField(max_length=120)
    area = models.CharField(max_length=40, db_index=True, help_text="One of SERVICE_AREAS")
    location_hint = models.CharField(
        max_length=160, help_text="Public, approximate: e.g. 'off Ntinda-Kisaasi Rd, near…'"
    )
    directions = models.TextField(help_text="Private. Only the assigned broker sees this.")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    rent_ugx = models.PositiveIntegerField(help_text="Monthly rent in UGX")
    bedrooms = models.PositiveSmallIntegerField(default=1)
    bathrooms = models.PositiveSmallIntegerField(default=1)
    furnished = models.BooleanField(default=False)
    description = models.TextField(blank=True)

    contact = models.ForeignKey(
        PropertyContact, on_delete=models.PROTECT, related_name="properties"
    )
    authority_document = models.FileField(
        storage=private_storage,
        upload_to=authority_upload_to,
        blank=True,
        help_text="Photo of the written permission to advertise (private).",
    )
    authority_sha256 = models.CharField(max_length=64, blank=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    sourced_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sourced_properties"
    )
    published_at = models.DateTimeField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    recheck_due_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = PropertyQuerySet.as_manager()

    class Meta:
        verbose_name_plural = "properties"
        ordering = ["-published_at", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rent_ugx__gt=0), name="property_rent_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} · {self.area}"

    @property
    def is_bookable(self) -> bool:
        return (
            self.status == self.Status.PUBLISHED
            and self.recheck_due_at is not None
            and self.recheck_due_at > timezone.now()
        )

    @property
    def latest_approved_verification(self) -> "Verification | None":
        return (
            self.verifications.filter(decision=Verification.Decision.APPROVED)
            .order_by("-checked_at")
            .first()
        )


class PropertyPhoto(PublicModel):
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to=photo_upload_to)
    caption = models.CharField(max_length=120, blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["position", "created_at"]


# What the verifier must confirm on site. Shown to customers as "what we checked".
CHECKLIST = {
    "authority": "Permission to advertise confirmed with the owner or their representative",
    "location": "Location matches the listing",
    "rent": "Rent and charges confirmed",
    "availability": "Available to rent now",
    "condition": "Condition matches the description",
    "photos": "Photos are of this property and current",
}


class Verification(PublicModel):
    """One inspection. Approval must come from someone other than the sourcer and verifier."""

    class Decision(models.TextChoices):
        PENDING = "pending", "Awaiting approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Sent back"

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="verifications")
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="verifications_done"
    )
    checked_at = models.DateTimeField(help_text="When the inspection happened")
    checklist = models.JSONField(help_text="CHECKLIST key -> true")
    notes = models.TextField(blank=True)
    decision = models.CharField(
        max_length=16, choices=Decision.choices, default=Decision.PENDING, db_index=True
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="verifications_decided",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-checked_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["property"],
                condition=models.Q(decision="pending"),
                name="one_pending_verification_per_property",
            ),
        ]

    def __str__(self) -> str:
        return f"Inspection of {self.property} on {self.checked_at:%d %b %Y}"

    @builtins.property
    def checked_items(self) -> list[str]:
        return [label for key, label in CHECKLIST.items() if self.checklist.get(key)]
