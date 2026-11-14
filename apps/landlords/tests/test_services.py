from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.core.crypto import hash_identifier
from apps.landlords import services
from apps.landlords.models import IdentityStatus, IdentitySubmission, LandlordProfile
from apps.moderation.models import BlockedIdentity
from apps.moderation.services import is_blocked

from .conftest import VALID_NIN

pytestmark = pytest.mark.django_db(transaction=True)  # on_commit SMS must fire

Status = IdentitySubmission.Status
Reason = IdentitySubmission.Reason


def _actions() -> list[str]:
    return list(AuditEvent.objects.order_by("created_at").values_list("action", flat=True))


# --- Submitting --------------------------------------------------------------


def test_submit_creates_pending_submission_and_audit(submit, landlord_user):
    submission = submit(landlord_user)

    profile = LandlordProfile.objects.get(user=landlord_user)
    assert profile.identity_status == IdentityStatus.PENDING
    assert submission.status == Status.PENDING
    assert submission.nin_last4 == "ABCD" and submission.nin_format_ok
    assert set(submission.document_hashes) == {"id_front", "id_back", "selfie"}
    assert submission.consent_version and submission.consented_at
    assert _actions() == ["identity.submitted"]


def test_full_nin_is_never_stored_in_readable_form(submit, landlord_user):
    submission = submit(landlord_user)
    profile = submission.profile
    stored = [profile.nin_hash, profile.nin_last4, submission.nin_hash, submission.nin_last4]
    assert VALID_NIN not in " ".join(stored)
    assert profile.nin_hash == hash_identifier(VALID_NIN)
    assert VALID_NIN not in str(AuditEvent.objects.get().evidence)


def test_documents_go_to_private_storage_with_random_names(submit, landlord_user):
    submission = submit(landlord_user)
    for field in ("id_front", "id_back", "selfie"):
        document = getattr(submission, field)
        assert document.storage is IdentitySubmission._meta.get_field(field).storage
        assert document.name.startswith(f"identity/{submission.profile_id}/")
        assert document.name.endswith(".jpg") and "photo" not in document.name


def test_cannot_submit_twice_while_pending(submit, landlord_user):
    submit(landlord_user)
    with pytest.raises(services.IdentityError, match="already being reviewed"):
        submit(landlord_user)


def test_same_nin_on_another_account_is_refused_and_audited(submit, landlord_user):
    submit(landlord_user)
    other = User.objects.create_user("+256701555444")
    with pytest.raises(services.IdentityError, match="another Crib Link account"):
        submit(other)
    assert "identity.duplicate_nin" in _actions()
    assert not IdentitySubmission.objects.filter(profile__user=other).exists()


def test_blocked_nin_or_phone_is_refused(submit, landlord_user):
    BlockedIdentity.objects.create(
        kind=BlockedIdentity.Kind.NIN_HASH, value=hash_identifier(VALID_NIN), reason="fraud"
    )
    with pytest.raises(services.IdentityError, match="can't accept"):
        submit(landlord_user)

    other = User.objects.create_user("+256701555444")
    BlockedIdentity.objects.create(kind=BlockedIdentity.Kind.PHONE, value=other.phone, reason="x")
    with pytest.raises(services.IdentityError, match="can't accept"):
        submit(other, nin="CF99999999ZZZZ")


# --- Reviewing ---------------------------------------------------------------


def test_approve_verifies_landlord_and_texts_them(submit, landlord_user, reviewer, sms_outbox):
    submission = submit(landlord_user)
    services.review_identity(reviewer=reviewer, submission=submission, decision=Status.APPROVED)

    submission.refresh_from_db()
    profile = submission.profile
    assert submission.status == Status.APPROVED and submission.reviewed_by == reviewer
    assert profile.is_id_verified and profile.id_verified_at
    assert "identity.approved" in _actions()
    assert sms_outbox[-1].to == landlord_user.phone
    assert "verified" in sms_outbox[-1].body


def test_needs_info_lets_landlord_resubmit(submit, landlord_user, reviewer, sms_outbox):
    first = submit(landlord_user)
    services.review_identity(
        reviewer=reviewer,
        submission=first,
        decision=Status.NEEDS_INFO,
        reason=Reason.PHOTO_UNCLEAR,
    )
    assert "blurry" in sms_outbox[-1].body
    second = submit(landlord_user, nin="CF12345678ABCD")  # corrected NIN is allowed

    assert second.pk != first.pk
    assert LandlordProfile.objects.get().identity_status == IdentityStatus.PENDING


def test_reject_records_reason_without_ban(submit, landlord_user, reviewer):
    submission = submit(landlord_user)
    services.review_identity(
        reviewer=reviewer, submission=submission, decision=Status.REJECTED, reason=Reason.ID_EXPIRED
    )
    landlord_user.refresh_from_db()
    assert landlord_user.is_active
    assert LandlordProfile.objects.get().identity_status == IdentityStatus.REJECTED
    assert not BlockedIdentity.objects.exists()


def test_fraud_bans_nin_and_phone_and_suspends_user(submit, landlord_user, reviewer, sms_outbox):
    submission = submit(landlord_user)
    sent_before = len(sms_outbox)
    services.review_identity(
        reviewer=reviewer,
        submission=submission,
        decision=Status.REJECTED,
        reason=Reason.FRAUD,
        note="Title deed belongs to someone else; LC1 confirmed.",
    )

    landlord_user.refresh_from_db()
    assert not landlord_user.is_active
    assert LandlordProfile.objects.get().identity_status == IdentityStatus.BANNED
    assert is_blocked(nin_hash=hash_identifier(VALID_NIN))
    assert is_blocked(phone=landlord_user.phone)
    assert {"identity.rejected", "user.banned", "moderation.identity_blocked"} <= set(_actions())
    assert len(sms_outbox) == sent_before  # no tip-off SMS on fraud

    newcomer = User.objects.create_user("+256701555444")
    with pytest.raises(services.IdentityError):
        submit(newcomer)  # same NIN on a fresh account stays blocked


@pytest.mark.parametrize(
    ("decision", "reason"),
    [
        (Status.APPROVED, Reason.PHOTO_UNCLEAR),  # approval takes no reason
        (Status.NEEDS_INFO, ""),  # needs a reason
        (Status.NEEDS_INFO, Reason.FRAUD),  # reason from the wrong group
        (Status.REJECTED, Reason.PHOTO_UNCLEAR),
        ("bogus", ""),
    ],
)
def test_decision_and_reason_must_match(submit, landlord_user, reviewer, decision, reason):
    submission = submit(landlord_user)
    with pytest.raises(services.IdentityError):
        services.review_identity(
            reviewer=reviewer, submission=submission, decision=decision, reason=reason
        )
    submission.refresh_from_db()
    assert submission.status == Status.PENDING


def test_only_superuser_can_review(submit, landlord_user):
    submission = submit(landlord_user)
    staff = User.objects.create_user("+256701000002", password="x" * 16, is_staff=True)
    with pytest.raises(PermissionDenied):
        services.review_identity(reviewer=staff, submission=submission, decision=Status.APPROVED)


def test_nobody_reviews_their_own_application(submit, reviewer):
    submission = submit(reviewer)
    with pytest.raises(PermissionDenied, match="own"):
        services.review_identity(reviewer=reviewer, submission=submission, decision=Status.APPROVED)


def test_decided_submission_cannot_be_decided_again(submit, landlord_user, reviewer):
    submission = submit(landlord_user)
    services.review_identity(reviewer=reviewer, submission=submission, decision=Status.APPROVED)
    with pytest.raises(services.IdentityError, match="already been decided"):
        services.review_identity(
            reviewer=reviewer,
            submission=submission,
            decision=Status.REJECTED,
            reason=Reason.FRAUD,
        )


# --- Documents & retention ---------------------------------------------------


def test_opening_a_document_is_audited(submit, landlord_user, reviewer):
    submission = submit(landlord_user)
    document = services.open_document(viewer=reviewer, submission=submission, kind="selfie")
    assert document.name == submission.selfie.name
    event = AuditEvent.objects.get(action="identity.document_viewed")
    assert event.actor == reviewer and event.evidence == {"document": "selfie"}


def test_documents_are_super_admin_only(submit, landlord_user):
    submission = submit(landlord_user)
    with pytest.raises(PermissionDenied):
        services.open_document(viewer=landlord_user, submission=submission, kind="id_front")


def test_unknown_document_kind_is_refused(submit, landlord_user, reviewer):
    submission = submit(landlord_user)
    with pytest.raises(services.IdentityError):
        services.open_document(viewer=reviewer, submission=submission, kind="nin_hash")


def test_selfies_are_purged_90_days_after_approval_only(submit, reviewer):
    old, recent, pending = (
        User.objects.create_user(phone)
        for phone in ("+256701000011", "+256701000012", "+256701000013")
    )
    old_sub = submit(old, nin="CM00000000000A")
    recent_sub = submit(recent, nin="CM00000000000B")
    pending_sub = submit(pending, nin="CM00000000000C")
    for sub in (old_sub, recent_sub):
        services.review_identity(reviewer=reviewer, submission=sub, decision=Status.APPROVED)
    IdentitySubmission.objects.filter(pk=old_sub.pk).update(
        reviewed_at=timezone.now() - timedelta(days=91)
    )
    selfie_name = old_sub.selfie.name
    storage = old_sub.selfie.storage

    assert services.purge_expired_selfies() == 1

    old_sub.refresh_from_db()
    assert not old_sub.selfie and old_sub.selfie_deleted_at
    assert not storage.exists(selfie_name)
    assert old_sub.id_front  # ID images are kept
    for untouched in (recent_sub, pending_sub):
        untouched.refresh_from_db()
        assert untouched.selfie
    assert "identity.selfie_purged" in _actions()
    with pytest.raises(services.IdentityError, match="no longer stored"):
        services.open_document(viewer=reviewer, submission=old_sub, kind="selfie")
    assert services.purge_expired_selfies() == 0  # idempotent


def test_scheduled_purge_task_is_registered_and_runs(settings):
    from apps.landlords.tasks import purge_expired_selfies
    from config.celery import app

    scheduled = settings.CELERY_BEAT_SCHEDULE["purge-expired-selfies"]["task"]
    assert scheduled == purge_expired_selfies.name
    assert scheduled in app.tasks
    assert purge_expired_selfies.delay().get() == 0
