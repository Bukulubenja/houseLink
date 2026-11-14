"""Phone numbers are the login identity, so they are normalised to E.164 everywhere."""

import phonenumbers
from django.conf import settings
from django.core.exceptions import ValidationError

DEFAULT_REGION = "UG"


def normalize_phone(raw: str) -> str:
    """Return the number in E.164 (e.g. +256772123456) or raise ValidationError.

    Only regions in settings.ALLOWED_PHONE_REGIONS are accepted, which stops attackers
    pumping SMS codes to premium-rate foreign numbers at our cost.
    """
    try:
        parsed = phonenumbers.parse((raw or "").strip(), DEFAULT_REGION)
    except phonenumbers.NumberParseException as exc:
        raise ValidationError("Enter a valid phone number.", code="invalid") from exc

    if not phonenumbers.is_valid_number(parsed):
        raise ValidationError("Enter a valid phone number.", code="invalid")
    if phonenumbers.region_code_for_number(parsed) not in settings.ALLOWED_PHONE_REGIONS:
        raise ValidationError("Only Ugandan phone numbers are supported.", code="region")
    if phonenumbers.number_type(parsed) not in (
        phonenumbers.PhoneNumberType.MOBILE,
        phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE,
    ):
        raise ValidationError("Enter a mobile number that can receive SMS.", code="not_mobile")

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def mask_phone(e164: str) -> str:
    """+256772123456 -> +256 77*** *456 — safe for logs and on-screen hints."""
    if len(e164) < 8:
        return "***"
    return f"{e164[:4]} {e164[4:6]}*** *{e164[-3:]}"
