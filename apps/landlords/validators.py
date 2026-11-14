import re

from django.core.exceptions import ValidationError

NIN_PATTERN = re.compile(r"^[A-Z0-9]{14}$")
# From the launch plan; not yet confirmed against real IDs, so it only flags, never blocks.
EXPECTED_NIN_PREFIXES = ("CM", "CF")


def normalize_nin(raw: str) -> str:
    value = "".join((raw or "").split()).upper()
    if not NIN_PATTERN.match(value):
        raise ValidationError("A NIN has 14 letters and numbers, e.g. CM12345678ABCD.")
    return value


def has_expected_nin_prefix(nin: str) -> bool:
    return nin.startswith(EXPECTED_NIN_PREFIXES)
