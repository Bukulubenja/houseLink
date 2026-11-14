from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class BlockedIdentity(TimeStampedModel):
    """A permanently banned identifier. NINs are stored as keyed hashes, phones in E.164."""

    class Kind(models.TextChoices):
        NIN_HASH = "nin_hash", "National ID (hashed)"
        PHONE = "phone", "Phone number"

    kind = models.CharField(max_length=16, choices=Kind.choices)
    value = models.CharField(max_length=128)
    reason = models.TextField()
    blocked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["kind", "value"], name="unique_blocked_identity")
        ]
        verbose_name_plural = "blocked identities"

    def __str__(self) -> str:
        shown = self.value if self.kind == self.Kind.PHONE else f"{self.value[:8]}…"
        return f"{self.get_kind_display()}: {shown}"
