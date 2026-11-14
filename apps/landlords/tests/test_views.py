import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.landlords.models import IdentitySubmission

from .conftest import VALID_NIN

pytestmark = pytest.mark.django_db

VERIFY = reverse("landlords:verify")
QUEUE = reverse("review:identity_queue")


def _form_data(upload, **overrides) -> dict:
    data = {
        "legal_name": "  Amina   Nakato ",
        "nin": VALID_NIN.lower(),
        "id_front": upload("front.png"),
        "id_back": upload("back.png"),
        "selfie": upload("selfie.png"),
        "consent": "on",
    }
    data.update(overrides)
    return data


# --- Landlord side -----------------------------------------------------------


def test_anonymous_user_is_sent_to_phone_sign_in(client):
    response = client.get(VERIFY)
    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


def test_form_shows_consent_explanation(client, landlord_user):
    client.force_login(landlord_user)
    response = client.get(VERIFY)
    assert b"Why we ask for your ID" in response.content
    assert b'enctype="multipart/form-data"' in response.content


def test_valid_submission_redirects_to_status(client, landlord_user, upload):
    client.force_login(landlord_user)
    response = client.post(VERIFY, _form_data(upload))
    assert response.status_code == 302

    submission = IdentitySubmission.objects.get()
    assert submission.legal_name == "Amina Nakato"
    page = client.get(VERIFY)
    assert b"Waiting for review" in page.content


def test_consent_is_required(client, landlord_user, upload):
    client.force_login(landlord_user)
    data = _form_data(upload)
    del data["consent"]
    response = client.post(VERIFY, data)
    assert response.status_code == 200
    assert not IdentitySubmission.objects.exists()


def test_non_image_upload_is_rejected(client, landlord_user, upload):
    from django.core.files.uploadedfile import SimpleUploadedFile

    client.force_login(landlord_user)
    fake = SimpleUploadedFile("id.png", b"%PDF-1.4 not an image", content_type="image/png")
    response = client.post(VERIFY, _form_data(upload, id_front=fake))
    assert b"Upload a photo" in response.content
    assert not IdentitySubmission.objects.exists()


def test_bad_nin_shows_error(client, landlord_user, upload):
    client.force_login(landlord_user)
    response = client.post(VERIFY, _form_data(upload, nin="123"))
    assert b"14 letters and numbers" in response.content


def test_oversized_request_is_refused_before_parsing(client, landlord_user, upload, settings):
    settings.IDENTITY_REQUEST_MAX_BYTES = 100
    client.force_login(landlord_user)
    response = client.post(VERIFY, _form_data(upload))
    assert response.status_code == 413


def test_needs_info_reason_is_shown_with_the_form(client, landlord_user, reviewer, submit):
    from apps.landlords import services

    submission = submit(landlord_user)
    services.review_identity(
        reviewer=reviewer,
        submission=submission,
        decision=IdentitySubmission.Status.NEEDS_INFO,
        reason=IdentitySubmission.Reason.SELFIE_UNCLEAR,
    )
    client.force_login(landlord_user)
    response = client.get(VERIFY)
    assert b"selfie must clearly show your face" in response.content
    assert b"Send for review" in response.content


# --- Super admin side --------------------------------------------------------


def test_review_queue_sends_anonymous_users_to_password_login(client):
    response = client.get(QUEUE)
    assert response.status_code == 302
    assert response.url.startswith(reverse("admin:login"))


@pytest.mark.parametrize("is_staff", [False, True])
def test_review_pages_are_hidden_from_non_superusers(client, landlord_user, submit, is_staff):
    submission = submit(landlord_user)
    viewer = User.objects.create_user("+256701000002", is_staff=is_staff)
    client.force_login(viewer)
    for url in (
        QUEUE,
        reverse("review:identity_detail", args=[submission.pk]),
        reverse("review:identity_document", args=[submission.pk, "id_front"]),
    ):
        assert client.get(url).status_code == 404
    assert not AuditEvent.objects.filter(action="identity.document_viewed").exists()


def test_queue_lists_pending_oldest_first(client, reviewer, submit):
    first = submit(User.objects.create_user("+256701000011"), nin="CM00000000000A")
    second = submit(User.objects.create_user("+256701000012"), nin="XX00000000000B")
    client.force_login(reviewer)
    html = client.get(QUEUE).content.decode()
    assert html.index(str(first.pk)) < html.index(str(second.pk))
    assert "Unusual NIN format" in html


def test_reviewer_approves_through_the_page(client, reviewer, landlord_user, submit):
    submission = submit(landlord_user)
    client.force_login(reviewer)
    url = reverse("review:identity_detail", args=[submission.pk])
    assert b"ending <strong>ABCD</strong>" in client.get(url).content

    response = client.post(url, {"decision": "approved", "reason": "", "note": "Looks good"})
    assert response.status_code == 302 and response.url == QUEUE
    submission.refresh_from_db()
    assert submission.status == "approved"


def test_review_form_rejects_mismatched_reason(client, reviewer, landlord_user, submit):
    submission = submit(landlord_user)
    client.force_login(reviewer)
    url = reverse("review:identity_detail", args=[submission.pk])
    response = client.post(url, {"decision": "needs_info", "reason": ""})
    assert b"Pick a reason" in response.content
    response = client.post(url, {"decision": "approved", "reason": "photo_unclear"})
    assert b"don&#x27;t take a reason" in response.content


def test_document_streams_privately_and_is_audited(client, reviewer, landlord_user, submit):
    submission = submit(landlord_user)
    client.force_login(reviewer)
    response = client.get(reverse("review:identity_document", args=[submission.pk, "id_back"]))

    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"
    assert "no-store" in response["Cache-Control"]
    assert b"".join(response.streaming_content)[:2] == b"\xff\xd8"  # JPEG magic
    assert AuditEvent.objects.filter(action="identity.document_viewed").count() == 1


def test_unknown_document_kind_is_404(client, reviewer, landlord_user, submit):
    submission = submit(landlord_user)
    client.force_login(reviewer)
    url = reverse("review:identity_document", args=[submission.pk, "nin_hash"])
    assert client.get(url).status_code == 404


@pytest.mark.django_db(transaction=True)
def test_duplicate_nin_via_form_is_refused_and_evidence_is_kept(client, upload, submit):
    """The refusal is shown, and the audit event survives the request transaction."""
    submit(User.objects.create_user("+256701000011"))
    intruder = User.objects.create_user("+256701000099")
    client.force_login(intruder)

    response = client.post(VERIFY, _form_data(upload))

    assert response.status_code == 200
    assert b"already linked to another" in response.content
    assert AuditEvent.objects.filter(action="identity.duplicate_nin").count() == 1
