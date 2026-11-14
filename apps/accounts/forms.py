from django import forms
from django.conf import settings

from apps.core.phone import normalize_phone


class PhoneForm(forms.Form):
    phone = forms.CharField(
        label="Phone number",
        max_length=20,
        widget=forms.TextInput(
            attrs={"inputmode": "tel", "autocomplete": "tel", "placeholder": "0772 123 456"}
        ),
    )

    def clean_phone(self) -> str:
        return normalize_phone(self.cleaned_data["phone"])


class CodeForm(forms.Form):
    code = forms.RegexField(
        label="Code",
        regex=rf"^\s*\d{{{settings.OTP_LENGTH}}}\s*$",
        error_messages={"invalid": f"Enter the {settings.OTP_LENGTH}-digit code."},
        widget=forms.TextInput(
            attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "maxlength": "8"}
        ),
    )
