import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.phone import normalize_phone


class UserManager(BaseUserManager["User"]):
    use_in_migrations = True

    def create_user(self, phone: str, password: str | None = None, **extra: object) -> "User":
        user = self.model(phone=normalize_phone(phone), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()  # tenants and landlords sign in by SMS code only
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_superuser(self, phone: str, password: str | None = None, **extra: object) -> "User":
        if not password:
            raise ValueError("Superusers must have a password.")
        extra.update(is_staff=True, is_superuser=True)
        return self.create_user(phone, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """A person. Roles come from related profiles (landlord, verifier, team), not flags here."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone = models.CharField(max_length=16, unique=True, help_text="E.164, e.g. +256772123456")
    full_name = models.CharField(max_length=150, blank=True)
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True, help_text="Untick to suspend the account.")
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS: list[str] = []

    def __str__(self) -> str:
        return self.full_name or self.phone

    def get_full_name(self) -> str:
        return self.full_name

    def get_short_name(self) -> str:
        return self.full_name.split(" ")[0] if self.full_name else ""


class OneTimeCode(models.Model):
    """A short-lived SMS code. Only an HMAC of the code is stored."""

    class Purpose(models.TextChoices):
        LOGIN = "login", "Sign in"
        VISIT_CONFIRM = "visit_confirm", "Confirm verifier visit"

    phone = models.CharField(max_length=16)
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    code_hash = models.CharField(max_length=128)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["phone", "purpose", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.get_purpose_display()} code for {self.phone}"

    @property
    def is_usable(self) -> bool:
        return self.consumed_at is None and self.expires_at > timezone.now()
