from django import forms
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.core.images import sanitize_image
from apps.core.phone import normalize_phone

from .models import CHECKLIST, Property, PropertyContact
from .services import PropertyDetails

PHOTO_ATTRS = {"accept": "image/jpeg,image/png,image/webp"}


def _area_choices() -> list[tuple[str, str]]:
    return [(area, area) for area in settings.SERVICE_AREAS]


class SearchForm(forms.Form):
    area = forms.ChoiceField(required=False)
    min_rent = forms.IntegerField(required=False, min_value=0, label="Min rent (UGX)")
    max_rent = forms.IntegerField(required=False, min_value=0, label="Max rent (UGX)")
    bedrooms = forms.IntegerField(required=False, min_value=0, max_value=10, label="Bedrooms (min)")
    kind = forms.ChoiceField(required=False, label="Type")

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["area"].choices = [("", "Any area"), *_area_choices()]
        self.fields["kind"].choices = [("", "Any type"), *Property.Kind.choices]


class ContactForm(forms.Form):
    name = forms.CharField(max_length=150)
    phone = forms.CharField(max_length=20)
    relationship = forms.ChoiceField(choices=PropertyContact.Relationship.choices)

    def clean_phone(self) -> str:
        return normalize_phone(self.cleaned_data["phone"])


class PropertyForm(forms.Form):
    title = forms.CharField(max_length=120)
    area = forms.ChoiceField()
    location_hint = forms.CharField(max_length=160, label="Public location hint")
    directions = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), label="Directions (private)"
    )
    kind = forms.ChoiceField(choices=Property.Kind.choices, label="Type")
    rent_ugx = forms.IntegerField(min_value=1, label="Monthly rent (UGX)")
    bedrooms = forms.IntegerField(min_value=0, max_value=20, initial=1)
    bathrooms = forms.IntegerField(min_value=0, max_value=20, initial=1)
    furnished = forms.BooleanField(required=False)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), required=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["area"].choices = _area_choices()

    def details(self) -> PropertyDetails:
        return PropertyDetails(
            **{name: self.cleaned_data[name] for name in PropertyDetails.__dataclass_fields__}
        )

    @classmethod
    def initial_from(cls, prop: Property) -> dict:
        return {name: getattr(prop, name) for name in PropertyDetails.__dataclass_fields__}


class NewPropertyForm(PropertyForm):
    contact = forms.ModelChoiceField(queryset=PropertyContact.objects.order_by("name"))
    authority_document = forms.FileField(
        required=False,
        label="Photo of written permission to advertise",
        widget=forms.FileInput(PHOTO_ATTRS),
    )

    def clean_authority_document(self) -> ContentFile | None:
        upload = self.cleaned_data.get("authority_document")
        if not upload:
            return None
        return sanitize_image(upload, max_bytes=settings.LISTING_PHOTO_MAX_BYTES)


class PhotoForm(forms.Form):
    image = forms.FileField(widget=forms.FileInput(PHOTO_ATTRS))
    caption = forms.CharField(max_length=120, required=False)

    def clean_image(self) -> ContentFile:
        return sanitize_image(
            self.cleaned_data["image"], max_bytes=settings.LISTING_PHOTO_MAX_BYTES
        )


class VerificationForm(forms.Form):
    checked_at = forms.DateTimeField(
        label="Inspected at",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M"],
    )
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["checked_at"].initial = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
        for key, label in CHECKLIST.items():
            self.fields[f"check_{key}"] = forms.BooleanField(label=label, required=True)

    def checklist(self) -> dict[str, bool]:
        return {key: bool(self.cleaned_data.get(f"check_{key}")) for key in CHECKLIST}


class DecisionForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[("approve", "Approve and publish"), ("reject", "Send back")],
        widget=forms.RadioSelect,
    )
    note = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)


class AvailabilityForm(forms.Form):
    action = forms.ChoiceField(choices=[("pause", "Pause"), ("rented", "Mark as rented")])
    reason = forms.CharField(max_length=200)
