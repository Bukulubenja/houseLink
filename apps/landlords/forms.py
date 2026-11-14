from django import forms
from django.conf import settings
from django.core.files.base import ContentFile

from apps.core.images import sanitize_image

from .models import REASONS_BY_DECISION, IdentitySubmission
from .validators import normalize_nin

PHOTO_ATTRS = {"accept": "image/jpeg,image/png,image/webp"}


class IdentityForm(forms.Form):
    legal_name = forms.CharField(
        label="Full name exactly as on your national ID", min_length=3, max_length=150
    )
    nin = forms.CharField(
        label="NIN (National Identification Number)",
        max_length=20,
        widget=forms.TextInput(attrs={"autocapitalize": "characters", "autocomplete": "off"}),
    )
    id_front = forms.FileField(label="Front of your ID", widget=forms.FileInput(PHOTO_ATTRS))
    id_back = forms.FileField(label="Back of your ID", widget=forms.FileInput(PHOTO_ATTRS))
    selfie = forms.FileField(
        label="A selfie of you holding your ID next to your face",
        widget=forms.FileInput({**PHOTO_ATTRS, "capture": "user"}),
    )
    consent = forms.BooleanField(
        label="I agree that Crib Link may store these documents to verify my identity."
    )

    def clean_legal_name(self) -> str:
        return " ".join(self.cleaned_data["legal_name"].split())

    def clean_nin(self) -> str:
        return normalize_nin(self.cleaned_data["nin"])

    def _clean_photo(self, field: str) -> ContentFile:
        return sanitize_image(
            self.cleaned_data[field], max_bytes=settings.IDENTITY_UPLOAD_MAX_BYTES
        )

    def clean_id_front(self) -> ContentFile:
        return self._clean_photo("id_front")

    def clean_id_back(self) -> ContentFile:
        return self._clean_photo("id_back")

    def clean_selfie(self) -> ContentFile:
        return self._clean_photo("selfie")


class ReviewForm(forms.Form):
    Status = IdentitySubmission.Status

    decision = forms.ChoiceField(
        choices=[
            (Status.APPROVED, "Approve"),
            (Status.NEEDS_INFO, "Needs more info"),
            (Status.REJECTED, "Reject"),
        ],
        widget=forms.RadioSelect,
    )
    reason = forms.ChoiceField(
        choices=[("", "— No reason (approve only) —"), *IdentitySubmission.Reason.choices],
        required=False,
    )
    note = forms.CharField(
        label="Internal note (evidence, LC1 call, etc.)",
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean(self) -> dict:
        data = super().clean()
        decision, reason = data.get("decision"), data.get("reason", "")
        if decision:
            allowed = REASONS_BY_DECISION[decision]
            if allowed and reason not in allowed:
                self.add_error("reason", "Pick a reason that fits this decision.")
            if not allowed and reason:
                self.add_error("reason", "Approvals don't take a reason.")
        return data
