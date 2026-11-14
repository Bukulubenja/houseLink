"""Untrusted image uploads are decoded and re-encoded, never stored as uploaded.

Re-encoding strips EXIF (phones embed GPS location), neutralises polyglot files that are
both an image and a script, and shrinks photos for users on expensive mobile data.
"""

from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
JPEG_QUALITY = 85


def sanitize_image(upload: UploadedFile, *, max_bytes: int) -> ContentFile:
    """Validate an uploaded photo and return a clean JPEG, or raise ValidationError."""
    if upload.size is None or upload.size > max_bytes:
        raise ValidationError(
            f"Photos must be smaller than {max_bytes // (1024 * 1024)} MB.", code="too_large"
        )

    try:
        upload.seek(0)
        with Image.open(upload) as probe:
            image_format = probe.format
            width, height = probe.size
            probe.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValidationError("Upload a photo (JPEG, PNG or WebP).", code="not_image") from exc

    if image_format not in ALLOWED_FORMATS:
        raise ValidationError("Upload a photo (JPEG, PNG or WebP).", code="format")
    if width * height > settings.IMAGE_MAX_PIXELS:
        raise ValidationError("This photo's resolution is too large.", code="too_many_pixels")

    upload.seek(0)
    try:
        with Image.open(upload) as original:
            image = ImageOps.exif_transpose(original)  # keep orientation before EXIF is dropped
            image = image.convert("RGB")
            image.thumbnail((settings.IMAGE_MAX_SIDE, settings.IMAGE_MAX_SIDE))
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except (OSError, ValueError) as exc:
        raise ValidationError("This photo could not be read.", code="corrupt") from exc

    return ContentFile(buffer.getvalue(), name="photo.jpg")
