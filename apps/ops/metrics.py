"""The weekly management dashboard from the operating plan (section 10)."""

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone

from apps.bookings.models import Appointment, Feedback, ViewingReport
from apps.listings.models import Property
from apps.payments.models import Payment, Refund

S = Appointment.Status


@dataclass(frozen=True)
class Metric:
    label: str
    value: str
    why: str


def _pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "-"


def weekly(days: int = 7) -> list[Metric]:
    now = timezone.now()
    since = now - timedelta(days=days)

    active = Property.objects.bookable().count()
    due_soon = Property.objects.filter(
        status=Property.Status.PUBLISHED, recheck_due_at__lte=now + timedelta(days=2)
    ).count()
    expired = Property.objects.filter(status=Property.Status.EXPIRED, updated_at__gte=since).count()

    window = Appointment.objects.filter(created_at__gte=since)
    requests = window.count()
    response = (
        window.filter(confirmed_at__isnull=False)
        .annotate(
            first_answer=ExpressionWrapper(
                F("confirmed_at") - F("created_at"), output_field=DurationField()
            )
        )
        .aggregate(avg=Avg("first_answer"))["avg"]
    )
    confirmed = window.filter(confirmed_at__isnull=False).count()
    paid = window.filter(paid_at__isnull=False).count()

    held = Appointment.objects.filter(scheduled_start__gte=since, scheduled_start__lte=now)
    done = held.filter(status=S.COMPLETED).count()
    no_show = held.filter(status=S.CUSTOMER_NO_SHOW).count()
    broker_no_show = held.filter(closed_reason=Appointment.CancelReason.BROKER_NO_SHOW).count()
    reported = ViewingReport.objects.filter(created_at__gte=since).count()
    flagged = ViewingReport.objects.filter(created_at__gte=since, property_as_listed=False).count()

    collected = (
        Payment.objects.filter(status=Payment.Status.SUCCEEDED, confirmed_at__gte=since).aggregate(
            total=Sum("amount_ugx")
        )["total"]
        or 0
    )
    refunds = Refund.objects.filter(created_at__gte=since)
    refunded = refunds.aggregate(total=Sum("amount_ugx"))["total"] or 0
    open_refunds = Refund.objects.filter(status=Refund.Status.REQUESTED).count()

    ratings = Feedback.objects.filter(created_at__gte=since).aggregate(
        avg=Avg("rating"), n=Count("id"), happy=Count("id", filter=Q(rating__gte=4))
    )
    repeat = (
        Appointment.objects.filter(paid_at__isnull=False)
        .values("customer")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .count()
    )

    response_text = "-"
    if response is not None:
        hours = response.total_seconds() / 3600
        response_text = f"{hours:.1f} h"

    return [
        Metric("Active verified properties", str(active), "Usable inventory, not uploads."),
        Metric(
            "Rechecks due in 2 days / expired",
            f"{due_soon} / {expired}",
            f"Listings expire {settings.LISTING_RECHECK_DAYS} days after inspection.",
        ),
        Metric("Viewing requests", str(requests), "Demand."),
        Metric("Time to confirm a request", response_text, "Service responsiveness."),
        Metric(
            "Confirmed → paid",
            f"{paid} of {confirmed} ({_pct(paid, confirmed)})",
            "Trust, price clarity and payment ease.",
        ),
        Metric(
            "Completed / customer no-shows / broker no-shows",
            f"{done} / {no_show} / {broker_no_show}",
            "Delivery reliability.",
        ),
        Metric("Reports filed / listings flagged", f"{reported} / {flagged}", "Field quality."),
        Metric(
            "Collected / refunded (UGX)",
            f"{collected:,} / {refunded:,}",
            f"{open_refunds} refunds waiting to be paid out.",
        ),
        Metric(
            "Feedback",
            f"{ratings['avg']:.1f} avg from {ratings['n']}" if ratings["n"] else "-",
            "Customer satisfaction.",
        ),
        Metric("Repeat customers (all time)", str(repeat), "Trust and word of mouth."),
    ]


def queue_counts() -> dict[str, int]:
    return {
        "awaiting_approval": Property.objects.filter(
            status=Property.Status.AWAITING_APPROVAL
        ).count()
        + Property.objects.filter(
            status=Property.Status.PUBLISHED, verifications__decision="pending"
        ).count(),
        "needs_broker": Appointment.objects.filter(
            needs_new_broker=True, status__in=[S.AWAITING_PAYMENT, S.BOOKED]
        ).count(),
        "requests": Appointment.objects.filter(status=S.REQUESTED).count(),
        "awaiting_payment": Appointment.objects.filter(status=S.AWAITING_PAYMENT).count(),
        "refunds": Refund.objects.filter(status=Refund.Status.REQUESTED).count(),
        "mismatches": Payment.objects.filter(status=Payment.Status.MISMATCH).count(),
    }
