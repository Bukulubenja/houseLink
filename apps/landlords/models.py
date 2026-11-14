import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import PublicModel
from apps.core.storage import private_storage


class IdentityStatus(models.TextChoices):
    NOT_SUBMITTED = "not_submitted", "Not submitted"
    PENDING = "pending", "Waiting for review"
    NEEDS_INFO = "needs_info", "Needs more information"
    APPROVED = "approved", "ID verified"
    REJECTED = "rejected", "Rejected"
    BANNED = "banned", "Banned"


SUBMITTABLE_STATUSES = frozenset(
    {IdentityStatus.NOT_SUBMITTED, IdentityStatus.NEEDS_INFO, IdentityStatus.REJECTED}
)


class LandlordProfile(PublicModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="landlord_profile"
    )
    legal_name = models.CharField(max_length=150, blank=True, help_text="As printed on the ID")
    # One NIN per landlord account; only a keyed hash and the last 4 characters are kept.
    nin_hash = models.CharField(max_length=64, unique=True, null=True, blank=True, editable=False)
    nin_last4 = models.CharField(max_length=4, blank=True)
    identity_status = models.CharField(
        max_length=16,
        choices=IdentityStatus.choices,
        default=IdentityStatus.NOT_SUBMITTED,
        db_index=True,
    )
    id_verified_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return self.legal_name or str(self.user)

    @property
    def can_submit_identity(self) -> bool:
        return self.identity_status in SUBMITTABLE_STATUSES

    @property
    def is_id_verified(self) -> bool:
        return self.identity_status == IdentityStatus.APPROVED


def identity_upload_to(instance: "IdentitySubmission", filename: str) -> str:
    # Random names: nothing user-supplied reaches the storage path.
    return f"identity/{instance.profile_id}/{uuid.uuid4().hex}.jpg"


class IdentitySubmission(PublicModel):
    """One application for the ID-verified badge. Resubmissions create new rows."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        NEEDS_INFO = "needs_info", "Needs more info"
        REJECTED = "rejected", "Rejected"

    class Reason(models.TextChoices):
        PHOTO_UNCLEAR = "photo_unclear", "The ID photos are blurry, dark or cut off"
        SIDES_WRONG = "sides_wrong", "The front and back of the ID are missing or swapped"
        SELFIE_UNCLEAR = "selfie_unclear", "The selfie must clearly show your face and the ID"
        NAME_MISMATCH = "name_mismatch", "The name you typed does not match the ID"
        NIN_MISMATCH = "nin_mismatch", "The NIN you typed does not match the ID"
        ID_EXPIRED = "id_expired", "The ID card has expired"
        FACE_MISMATCH = "face_mismatch", "The selfie does not match the ID photo"
        DOCUMENT_ALTERED = "document_altered", "The ID appears to be edited or not genuine"
        FRAUD = "fraud", "Confirmed fraud"

    profile = models.ForeignKey(
        LandlordProfile, on_delete=models.PROTECT, related_name="submissions"
    )
    legal_name = models.CharField(max_length=150)
    nin_hash = models.CharField(max_length=64, editable=False)
    nin_last4 = models.CharField(max_length=4)
    nin_format_ok = models.BooleanField(help_text="Starts with CM/CF (format not yet confirmed)")

    id_front = models.FileField(storage=private_storage, upload_to=identity_upload_to)
    id_back = models.FileField(storage=private_storage, upload_to=identity_upload_to)
    selfie = models.FileField(storage=private_storage, upload_to=identity_upload_to, blank=True)
    selfie_deleted_at = models.DateTimeField(null=True, blank=True)
    document_hashes = models.JSONField(default=dict, help_text="SHA-256 of each stored image")

    consent_version = models.CharField(max_length=32)
    consented_at = models.DateTimeField()

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    reason = models.CharField(max_length=32, choices=Reason.choices, blank=True)
    reviewer_note = models.TextField(blank=True, help_text="Internal; never shown to landlord")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["profile"],
                condition=Q(status="pending"),
                name="one_pending_identity_submission_per_profile",
            )
        ]

    def __str__(self) -> str:
        return f"ID check for {self.legal_name} ({self.get_status_display()})"


DOCUMENT_FIELDS = ("id_front", "id_back", "selfie")

REASONS_BY_DECISION: dict[str, frozenset[str]] = {
    IdentitySubmission.Status.APPROVED: frozenset(),
    IdentitySubmission.Status.NEEDS_INFO: frozenset(
        {
            IdentitySubmission.Reason.PHOTO_UNCLEAR,
            IdentitySubmission.Reason.SIDES_WRONG,
            IdentitySubmission.Reason.SELFIE_UNCLEAR,
            IdentitySubmission.Reason.NAME_MISMATCH,
            IdentitySubmission.Reason.NIN_MISMATCH,
        }
    ),
    IdentitySubmission.Status.REJECTED: frozenset(
        {
            IdentitySubmission.Reason.ID_EXPIRED,
            IdentitySubmission.Reason.FACE_MISMATCH,
            IdentitySubmission.Reason.DOCUMENT_ALTERED,
            IdentitySubmission.Reason.FRAUD,
        }
    ),
}
