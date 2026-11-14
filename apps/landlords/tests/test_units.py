"""Pure helpers: image sanitising, NIN rules, keyed hashing."""

from io import BytesIO

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.core.crypto import hash_identifier
from apps.core.images import sanitize_image
from apps.landlords.validators import has_expected_nin_prefix, normalize_nin

from .conftest import make_image_bytes

MB = 1024 * 1024


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_allowed_formats_become_clean_jpeg(upload, fmt):
    result = sanitize_image(upload(fmt=fmt), max_bytes=MB)
    with Image.open(BytesIO(result.read())) as image:
        assert image.format == "JPEG"


def test_exif_including_gps_is_stripped():
    exif = Image.Exif()
    exif[0x8825] = {2: (0.0, 20.0, 0.0)}  # GPSInfo: latitude
    exif[0x010F] = "PhoneMaker"
    raw = make_image_bytes("JPEG", exif=exif.tobytes())
    with Image.open(BytesIO(raw)) as original:
        assert original.getexif()  # precondition: the upload carries EXIF

    result = sanitize_image(SimpleUploadedFile("p.jpg", raw), max_bytes=MB)
    with Image.open(BytesIO(result.read())) as clean:
        assert not clean.getexif()


def test_large_photos_are_resized(upload, settings):
    settings.IMAGE_MAX_SIDE = 100
    result = sanitize_image(upload(size=(400, 200)), max_bytes=MB)
    with Image.open(BytesIO(result.read())) as image:
        assert image.size == (100, 50)


def test_non_image_is_rejected():
    fake = SimpleUploadedFile("id.png", b"<script>alert(1)</script>", content_type="image/png")
    with pytest.raises(ValidationError) as exc:
        sanitize_image(fake, max_bytes=MB)
    assert exc.value.code == "not_image"


def test_disallowed_format_is_rejected(upload):
    with pytest.raises(ValidationError) as exc:
        sanitize_image(upload(fmt="GIF"), max_bytes=MB)
    assert exc.value.code == "format"


def test_oversized_file_is_rejected(upload):
    with pytest.raises(ValidationError) as exc:
        sanitize_image(upload(), max_bytes=10)
    assert exc.value.code == "too_large"


def test_pixel_bomb_is_rejected(upload, settings):
    settings.IMAGE_MAX_PIXELS = 100
    with pytest.raises(ValidationError) as exc:
        sanitize_image(upload(size=(20, 20)), max_bytes=MB)
    assert exc.value.code == "too_many_pixels"


@pytest.mark.parametrize("raw", ["CM12345678ABCD", "cm12345678abcd", " CM1234 5678 ABCD "])
def test_nin_normalises(raw):
    assert normalize_nin(raw) == "CM12345678ABCD"


@pytest.mark.parametrize("raw", ["", "CM123", "CM12345678ABCDE", "CM12345678ABC!"])
def test_bad_nin_is_rejected(raw):
    with pytest.raises(ValidationError):
        normalize_nin(raw)


def test_nin_prefix_is_flagged_not_enforced():
    assert has_expected_nin_prefix("CF12345678ABCD")
    assert not has_expected_nin_prefix("XX12345678ABCD")
    assert normalize_nin("XX12345678ABCD") == "XX12345678ABCD"


def test_identifier_hash_is_keyed_and_normalised(settings):
    first = hash_identifier("CM12345678ABCD")
    assert first == hash_identifier(" cm12345678abcd ")
    settings.PII_HASH_KEY = "another-key"
    assert hash_identifier("CM12345678ABCD") != first


def test_identifier_hash_requires_key(settings):
    settings.PII_HASH_KEY = ""
    with pytest.raises(ImproperlyConfigured):
        hash_identifier("CM12345678ABCD")
