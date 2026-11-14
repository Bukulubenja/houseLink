from collections.abc import Callable
from io import BytesIO

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.accounts.models import User
from apps.landlords import services
from apps.landlords.models import IdentitySubmission

VALID_NIN = "CM12345678ABCD"


def make_image_bytes(fmt: str = "PNG", size: tuple[int, int] = (64, 48), **save_kwargs) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (30, 120, 80)).save(buffer, format=fmt, **save_kwargs)
    return buffer.getvalue()


@pytest.fixture
def upload() -> Callable[..., SimpleUploadedFile]:
    def build(name: str = "photo.png", fmt: str = "PNG", **kwargs) -> SimpleUploadedFile:
        return SimpleUploadedFile(name, make_image_bytes(fmt, **kwargs), content_type="image/png")

    return build


@pytest.fixture
def clean_image() -> Callable[[], ContentFile]:
    return lambda: ContentFile(make_image_bytes("JPEG"), name="photo.jpg")


@pytest.fixture
def landlord_user() -> User:
    return User.objects.create_user("+256772123456", full_name="Amina Nakato")


@pytest.fixture
def reviewer() -> User:
    return User.objects.create_superuser("+256700000001", password="a-long-admin-password")


@pytest.fixture
def submit(clean_image) -> Callable[..., IdentitySubmission]:
    def run(user: User, nin: str = VALID_NIN, legal_name: str = "Amina Nakato"):
        return services.submit_identity(
            user=user,
            legal_name=legal_name,
            nin=nin,
            id_front=clean_image(),
            id_back=clean_image(),
            selfie=clean_image(),
        )

    return run
