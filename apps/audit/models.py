"""Append-only evidence trail. Corrections are new events; nothing is edited or deleted.

Enforced three times: here in the model, in the QuerySet, and by a PostgreSQL trigger
(migration 0002) so even raw SQL or a bug elsewhere cannot rewrite history.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class AppendOnlyError(Exception):
    pass


class AuditEventQuerySet(models.QuerySet):
    def update(self, **kwargs: object) -> int:
        raise AppendOnlyError("Audit events cannot be updated.")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError("Audit events cannot be deleted.")


class AuditEvent(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,  # users with history are suspended, never deleted
        null=True,
        blank=True,
        related_name="+",
        help_text="Empty for automatic actions (fraud flags, expiry jobs).",
    )
    action = models.CharField(max_length=64, help_text="e.g. landlord.approved")
    target_type = models.CharField(max_length=64)
    target_id = models.CharField(max_length=64)
    evidence = models.JSONField(default=dict, blank=True)
    evidence_hash = models.CharField(max_length=64, help_text="SHA-256 of the canonical record")
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    objects = AuditEventQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["target_type", "target_id", "-created_at"]),
            models.Index(fields=["action", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} on {self.target_type}:{self.target_id}"

    def save(self, *args: object, **kwargs: object) -> None:
        if not self._state.adding:
            raise AppendOnlyError("Audit events cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args: object, **kwargs: object) -> tuple[int, dict[str, int]]:
        raise AppendOnlyError("Audit events cannot be deleted.")
