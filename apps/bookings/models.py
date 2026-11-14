"""Viewing appointments.

    REQUESTED --confirm (slot + broker)--> AWAITING_PAYMENT --paid (provider-confirmed)--> BOOKED
    BOOKED --broker checks in + files report--> COMPLETED | CUSTOMER_NO_SHOW
    REQUESTED --decline--> DECLINED          AWAITING_PAYMENT --window passes--> LAPSED
    any open status --cancel--> CANCELLED (a paid booking gets a refund per the rules)

Payment records live in `payments`, so retries, partial failures and refunds never rewrite
the appointment's history.
"""

import builtins
import secrets

from django.conf import settings
from django.db import models

from apps.core.models import PublicModel

REFERENCE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: read over the phone


def new_reference() -> str:
    return "CL" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))


class Appointment(PublicModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        AWAITING_PAYMENT = "awaiting_payment", "Confirmed, awaiting payment"
        BOOKED = "booked", "Booked"
        COMPLETED = "completed", "Completed"
        CUSTOMER_NO_SHOW = "customer_no_show", "Customer did not attend"
        DECLINED = "declined", "Could not be arranged"
        LAPSED = "lapsed", "Not paid in time"
        CANCELLED = "cancelled", "Cancelled"

    class CancelReason(models.TextChoices):
        CUSTOMER = "customer", "Cancelled by customer"
        PROPERTY_UNAVAILABLE = "property_unavailable", "Property no longer available"
        ACCESS_DENIED = "access_denied", "Property access could not be arranged"
        BROKER_UNAVAILABLE = "broker_unavailable", "Broker unavailable"
        BROKER_NO_SHOW = "broker_no_show", "Broker did not attend"
        OTHER = "other", "Other"

    reference = models.CharField(max_length=10, unique=True, default=new_reference, editable=False)
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="appointments"
    )
    property = models.ForeignKey(
        "listings.Property", on_delete=models.PROTECT, related_name="appointments"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.REQUESTED, db_index=True
    )

    preferred_start = models.DateTimeField()
    customer_note = models.CharField(max_length=300, blank=True)

    scheduled_start = models.DateTimeField(null=True, blank=True, db_index=True)
    broker = models.ForeignKey(
        "brokers.BrokerProfile",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="appointments",
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    fee_ugx = models.PositiveIntegerField(null=True, blank=True, help_text="Fixed at confirmation")
    payment_due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    closed_reason = models.CharField(max_length=32, choices=CancelReason.choices, blank=True)
    closed_note = models.CharField(max_length=300, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    closed_at = models.DateTimeField(null=True, blank=True)

    checked_in_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    needs_new_broker = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Set when the assigned broker is suspended; cleared on reassignment.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["broker", "scheduled_start"])]

    def __str__(self) -> str:
        return f"{self.reference} · {self.property}"

    @builtins.property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @builtins.property
    def is_upcoming(self) -> bool:
        return self.status in {self.Status.AWAITING_PAYMENT, self.Status.BOOKED}


OPEN_STATUSES = frozenset(
    {
        Appointment.Status.REQUESTED,
        Appointment.Status.AWAITING_PAYMENT,
        Appointment.Status.BOOKED,
    }
)
# Statuses that hold a broker's time.
SLOT_HOLDING_STATUSES = frozenset({Appointment.Status.AWAITING_PAYMENT, Appointment.Status.BOOKED})


class ViewingReport(PublicModel):
    class Interest(models.TextChoices):
        WANTS_TO_RENT = "wants_to_rent", "Wants to rent it"
        UNDECIDED = "undecided", "Undecided"
        NOT_INTERESTED = "not_interested", "Not interested"
        NOT_APPLICABLE = "na", "Not applicable (customer absent)"

    appointment = models.OneToOneField(Appointment, on_delete=models.PROTECT, related_name="report")
    broker = models.ForeignKey("brokers.BrokerProfile", on_delete=models.PROTECT, related_name="+")
    customer_attended = models.BooleanField()
    property_as_listed = models.BooleanField(
        help_text="False flags the listing for a staff recheck."
    )
    discrepancy_note = models.TextField(blank=True)
    customer_interest = models.CharField(max_length=16, choices=Interest.choices)
    notes = models.TextField(blank=True)

    def __str__(self) -> str:
        return f"Report for {self.appointment.reference}"


class Feedback(PublicModel):
    appointment = models.OneToOneField(
        Appointment, on_delete=models.PROTECT, related_name="feedback"
    )
    rating = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True, max_length=1000)
    may_publish = models.BooleanField(
        default=False, help_text="Customer consented to this being used as a testimonial"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rating__gte=1, rating__lte=5), name="feedback_rating_1_to_5"
            )
        ]
