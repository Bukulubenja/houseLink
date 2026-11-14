import hashlib
import hmac

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def hash_identifier(value: str) -> str:
    """Keyed HMAC-SHA256 of an identifier (NIN, blocked phone).

    A plain hash of a 14-character NIN could be brute-forced from a leaked database; the
    separate PII_HASH_KEY makes that impossible without also stealing the key.
    """
    key = settings.PII_HASH_KEY
    if not key:
        raise ImproperlyConfigured("PII_HASH_KEY is not set.")
    normalized = "".join(value.split()).upper()
    return hmac.new(key.encode(), normalized.encode(), hashlib.sha256).hexdigest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
