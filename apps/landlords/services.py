"""Landlord identity verification: submit, review, view documents, purge selfies.

Every decision writes an AuditEvent in the same transaction as the change it records.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models.fields.files import FieldFile
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record
from apps.core.crypto import hash_identifier, sha256_hex
from apps.core.tasks import send_sms_task
from apps.moderation import services as moderation
from apps.moderation.models import BlockedIdentity

from .models import (
    DOCUMENT_FIELDS,
    REASONS_BY_DECISION,
    IdentityStatus,
    IdentitySubmission,
    LandlordProfile,
)
from .validators import has_expected_nin_prefix, normalize_nin

logger = logging.getLogger(__name__)

Status = IdentitySubmission.Status
Reason = IdentitySubmission.Reason

GENERIC_REFUSAL = "We can't accept this application. Please contact Crib Link support."
DECISION_SMS = {
    Status.APPROVED: "Crib Link: your ID is verified. Our property team will contact you to "
    "inspect and list your home.",
    Status.NEEDS_INFO: "Crib Link: we need more information to verify your ID: {reason}. "
    "Open Crib Link to update your application.",
    Status.REJECTED: "Crib Link: we could not verify your ID. "
    "Contact support if you think this is a mistake.",
}


class IdentityError(Exception):
    """User-facing failure; str(error) is safe to display."""


def _queue_sms(phone: str, body: str) -> None:
    transaction.on_commit(lambda: send_sms_task.delay(phone, body))


# --- Landlord side ----------------------------------------------------------


def submit_identity(
    *,
    user: User,
    legal_name: str,
    nin: str,
    id_front: ContentFile,
    id_back: ContentFile,
    selfie: ContentFile,
) -> IdentitySubmission:
    """Create a pending submission. Images must already be sanitised (core.images)."""
    nin = normalize_nin(nin)
    nin_hash = hash_identifier(nin)

    if moderation.is_blocked(nin_hash=nin_hash, phone=user.phone):
        raise IdentityError(GENERIC_REFUSAL)

    profile, _ = LandlordProfile.objects.get_or_create(user=user)
    clash = LandlordProfile.objects.filter(nin_hash=nin_hash).exclude(pk=profile.pk).first()
    if clash is not None:
        # Recorded outside the submission transaction so the evidence survives the refusal.
        record(
            actor=user,
            action="identity.duplicate_nin",
            target=profile,
            evidence={"existing_profile": str(clash.pk)},
        )
        raise IdentityError(
            "This national ID is already linked to another Crib Link account. "
            "Please contact support."
        )

    images = {"id_front": id_front, "id_back": id_back, "selfie": selfie}
    hashes = {name: sha256_hex(image.read()) for name, image in images.items()}
    for image in images.values():
        image.seek(0)

    try:
        with transaction.atomic():
            profile = LandlordProfile.objects.select_for_update().get(pk=profile.pk)
            if not profile.can_submit_identity:
                raise IdentityError("Your ID is already being reviewed or is verified.")

            profile.legal_name = legal_name
            profile.nin_hash = nin_hash
            profile.nin_last4 = nin[-4:]
            profile.identity_status = IdentityStatus.PENDING
            profile.save()

            submission = IdentitySubmission.objects.create(
                profile=profile,
                legal_name=legal_name,
                nin_hash=nin_hash,
                nin_last4=nin[-4:],
                nin_format_ok=has_expected_nin_prefix(nin),
                document_hashes=hashes,
                consent_version=settings.IDENTITY_CONSENT_VERSION,
                consented_at=timezone.now(),
                **images,
            )
            record(
                actor=user,
                action="identity.submitted",
                target=submission,
                evidence={"nin_last4": nin[-4:], "document_sha256": hashes},
            )
    except IntegrityError as exc:  # concurrent duplicate NIN or double submit
        raise IdentityError(GENERIC_REFUSAL) from exc

    logger.info("Identity submission %s created", submission.pk)
    return submission


# --- Super admin side -------------------------------------------------------


def _require_reviewer(reviewer: User, submission: IdentitySubmission) -> None:
    if not reviewer.is_superuser:
        raise PermissionDenied("Only the super admin reviews identity documents.")
    if submission.profile.user_id == reviewer.pk:
        raise PermissionDenied("Nobody may review their own application.")


def review_identity(
    *,
    reviewer: User,
    submission: IdentitySubmission,
    decision: str,
    reason: str = "",
    note: str = "",
) -> IdentitySubmission:
    """Approve, ask for more info, or reject (with a ready-made reason)."""
    _require_reviewer(reviewer, submission)
    if decision not in REASONS_BY_DECISION:
        raise IdentityError("Unknown decision.")
    allowed = REASONS_BY_DECISION[decision]
    if (reason or allowed) and reason not in allowed:
        raise IdentityError("Pick a reason that matches the decision.")

    with transaction.atomic():
        submission = (
            IdentitySubmission.objects.select_for_update()
            .select_related("profile__user")
            .get(pk=submission.pk)
        )
        if submission.status != Status.PENDING:
            raise IdentityError("This application has already been decided.")
        profile = LandlordProfile.objects.select_for_update().get(pk=submission.profile_id)
        landlord = profile.user
        now = timezone.now()

        submission.status = decision
        submission.reason = reason
        submission.reviewer_note = note
        submission.reviewed_by = reviewer
        submission.reviewed_at = now
        submission.save()

        is_fraud = reason == Reason.FRAUD
        profile.identity_status = IdentityStatus.BANNED if is_fraud else decision
        if decision == Status.APPROVED:
            profile.id_verified_at = now
        profile.save()

        record(
            actor=reviewer,
            action=f"identity.{decision}",
            target=submission,
            evidence={
                "reason": reason,
                "note": note,
                "document_sha256": submission.document_hashes,
            },
        )

        if is_fraud:
            _ban(landlord, profile, reviewer)
        else:
            reason_text = Reason(reason).label.lower() if reason else ""
            _queue_sms(landlord.phone, DECISION_SMS[decision].format(reason=reason_text))

    return submission


def _ban(landlord: User, profile: LandlordProfile, reviewer: User) -> None:
    reason = f"Confirmed identity fraud (landlord profile {profile.pk})"
    moderation.block(
        kind=BlockedIdentity.Kind.NIN_HASH, value=profile.nin_hash, reason=reason, actor=reviewer
    )
    moderation.block(
        kind=BlockedIdentity.Kind.PHONE, value=landlord.phone, reason=reason, actor=reviewer
    )
    landlord.is_active = False
    landlord.save(update_fields=["is_active"])
    record(actor=reviewer, action="user.banned", target=landlord, evidence={"reason": reason})


def open_document(*, viewer: User, submission: IdentitySubmission, kind: str) -> FieldFile:
    """Return an ID image for the super admin; every view is audited."""
    _require_reviewer(viewer, submission)
    if kind not in DOCUMENT_FIELDS:
        raise IdentityError("Unknown document.")
    document: FieldFile = getattr(submission, kind)
    if not document:
        raise IdentityError("This document is no longer stored.")
    record(
        actor=viewer,
        action="identity.document_viewed",
        target=submission,
        evidence={"document": kind},
    )
    return document


def purge_expired_selfies() -> int:
    """Delete selfies 90 days after approval (Data Protection and Privacy Act 2019)."""
    cutoff = timezone.now() - timedelta(days=settings.IDENTITY_SELFIE_RETENTION_DAYS)
    due = IdentitySubmission.objects.filter(
        status=Status.APPROVED, reviewed_at__lte=cutoff, selfie_deleted_at__isnull=True
    ).exclude(selfie="")

    purged = 0
    for submission in due.iterator():
        with transaction.atomic():
            submission.selfie.delete(save=False)
            submission.selfie = ""
            submission.selfie_deleted_at = timezone.now()
            submission.save(update_fields=["selfie", "selfie_deleted_at", "updated_at"])
            record(actor=None, action="identity.selfie_purged", target=submission)
        purged += 1
    if purged:
        logger.info("Purged %s expired selfies", purged)
    return purged
