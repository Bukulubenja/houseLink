import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User

pytestmark = pytest.mark.django_db


def test_create_user_normalises_phone_and_has_no_usable_password():
    user = User.objects.create_user("0772 123 456")
    assert user.phone == "+256772123456"
    assert not user.has_usable_password()
    assert not user.is_staff


def test_create_user_rejects_invalid_phone():
    with pytest.raises(ValidationError):
        User.objects.create_user("12345")


def test_phone_is_unique():
    User.objects.create_user("+256772123456")
    with pytest.raises(ValidationError):
        User.objects.create_user("0772123456")


def test_create_superuser_requires_password():
    with pytest.raises(ValueError):
        User.objects.create_superuser("+256772123456", password=None)


def test_create_superuser_sets_flags_and_password():
    admin = User.objects.create_superuser("+256772123456", password="a-long-admin-password")
    assert admin.is_staff and admin.is_superuser
    assert admin.check_password("a-long-admin-password")


def test_names():
    user = User(phone="+256772123456", full_name="Amina Nakato")
    assert str(user) == "Amina Nakato"
    assert user.get_full_name() == "Amina Nakato"
    assert user.get_short_name() == "Amina"
    assert str(User(phone="+256772123456")) == "+256772123456"
    assert User(phone="+256772123456").get_short_name() == ""
