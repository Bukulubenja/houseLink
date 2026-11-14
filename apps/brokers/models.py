from django.conf import settings
from django.db import models

from apps.core.models import PublicModel


class BrokerProfile(PublicModel):
    """A vetted field broker who accompanies customers. Signs in by phone like a customer;
    sees a customer's contact details only for appointments assigned to them and paid."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="broker_profile"
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    id_checked_at = models.DateTimeField(help_text="When staff checked the broker's national ID")
    terms_signed_at = models.DateTimeField(help_text="When the written broker agreement was signed")
    areas = models.JSONField(default=list, blank=True, help_text="SERVICE_AREAS they cover")
    onboarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )

    def __str__(self) -> str:
        return str(self.user)

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE and self.user.is_active
