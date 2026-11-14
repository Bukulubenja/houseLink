import secrets

from django.conf import settings
from django.db import models

from apps.bookings.models import REFERENCE_ALPHABET
from apps.core.models import PublicModel


def new_payment_reference() -> str:
    return "P" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(9))


class Payment(PublicModel):
    """One attempt to pay for an appointment. A paid status only ever comes from a
    provider-signed webhook or from finance recording a transaction seen on the statement."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        MISMATCH = "mismatch", "Amount mismatch (finance to resolve)"

    appointment = models.ForeignKey(
        "bookings.Appointment", on_delete=models.PROTECT, related_name="payments"
    )
    reference = models.CharField(max_length=12, unique=True, default=new_payment_reference)
    provider = models.CharField(max_length=32)
    amount_ugx = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    # NULL (not "") until known, so the conditional unique constraint ignores pending rows.
    provider_txn_id = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    amount_received_ugx = models.PositiveIntegerField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Finance staff who recorded a manual payment",
    )
    reconciled_at = models.DateTimeField(null=True, blank=True)
    reconciled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    statement_ref = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_txn_id"],
                condition=models.Q(provider_txn_id__isnull=False),
                name="unique_provider_transaction",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reference} · UGX {self.amount_ugx:,} · {self.status}"


class Refund(PublicModel):
    """Full refund of one payment. Whoever requests it may not also mark it paid."""

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        PAID = "paid", "Paid out"

    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="refund")
    amount_ugx = models.PositiveIntegerField()
    reason = models.CharField(max_length=200)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.REQUESTED, db_index=True
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Empty when the system requested it (e.g. an automatic cancellation rule)",
    )
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    payout_ref = models.CharField(max_length=64, blank=True)

    def __str__(self) -> str:
        return f"Refund of {self.payment.reference} · {self.status}"


class WebhookEvent(models.Model):
    """Every accepted provider event, stored once: a replayed event id is ignored."""

    provider = models.CharField(max_length=32)
    event_id = models.CharField(max_length=128)
    body_sha256 = models.CharField(max_length=64)
    outcome = models.CharField(max_length=32)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "event_id"], name="unique_webhook_event")
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.event_id} ({self.outcome})"
