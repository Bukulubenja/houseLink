import pytest
from django.core.exceptions import ValidationError

from apps.core.phone import mask_phone, normalize_phone


@pytest.mark.parametrize(
    "raw",
    ["0772123456", "0772 123 456", "+256772123456", "256772123456", " +256 772-123-456 "],
)
def test_ugandan_mobile_formats_normalise_to_e164(raw):
    assert normalize_phone(raw) == "+256772123456"


@pytest.mark.parametrize("raw", ["", "abc", "0772", "+2567721234567890"])
def test_invalid_numbers_are_rejected(raw):
    with pytest.raises(ValidationError) as exc:
        normalize_phone(raw)
    assert exc.value.code == "invalid"


def test_foreign_numbers_are_rejected_to_prevent_sms_toll_fraud():
    with pytest.raises(ValidationError) as exc:
        normalize_phone("+447911123456")
    assert exc.value.code == "region"


def test_ugandan_landline_is_rejected_because_it_cannot_receive_sms():
    with pytest.raises(ValidationError) as exc:
        normalize_phone("+256414123456")
    assert exc.value.code == "not_mobile"


def test_mask_phone_hides_the_middle_digits():
    assert mask_phone("+256772123456") == "+256 77*** *456"
    assert mask_phone("123") == "***"
