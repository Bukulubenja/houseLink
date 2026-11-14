"""Staff operations: listings pipeline, booking desk, brokers and finance.

Views only parse input and render; every rule (roles, separation of duties, state changes,
audit) is enforced in the services they call.
"""

from collections.abc import Callable

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.audit.services import record
from apps.bookings import services as bookings
from apps.bookings.forms import CloseForm, ConfirmForm
from apps.bookings.models import Appointment
from apps.brokers import services as brokers
from apps.brokers.models import BrokerProfile
from apps.core.roles import Role, has_role, role_required, roles_of
from apps.listings import services as listings
from apps.listings.forms import (
    AvailabilityForm,
    ContactForm,
    DecisionForm,
    NewPropertyForm,
    PhotoForm,
    PropertyForm,
    VerificationForm,
)
from apps.listings.models import Property, Verification
from apps.payments import services as payments
from apps.payments.models import Payment, Refund

from . import metrics
from .forms import (
    BrokerForm,
    BrokerStatusForm,
    ManualPaymentForm,
    PayoutForm,
    ReconcileForm,
    ResolveForm,
)

ANY_ROLE = tuple(Role)
LISTING_ROLES = (Role.ACQUISITION, Role.VERIFICATION, Role.APPROVAL, Role.BOOKINGS)
PAGE_SIZE = 30
UserErrors = (
    listings.ListingError,
    bookings.BookingError,
    brokers.BrokerError,
    payments.PaymentError,
    PermissionDenied,
)


FAILED = object()


def _attempt(request: HttpRequest, form, action: Callable[[], object], success: str) -> object:
    """Run a service call and return its result, or FAILED after attaching the user-facing
    error to the form (or flashing it when there is no form)."""
    try:
        result = action()
    except UserErrors as exc:
        if form is not None:
            form.add_error(None, str(exc))
        else:
            messages.error(request, str(exc))
        return FAILED
    messages.success(request, success)
    return result


@role_required(*ANY_ROLE)
@require_GET
def dashboard(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "ops/dashboard.html",
        {
            "metrics": metrics.weekly(),
            "queues": metrics.queue_counts(),
            "roles": roles_of(request.user),
        },
    )


# --- Listings ------------------------------------------------------------------


@role_required(*LISTING_ROLES)
@require_GET
def property_list(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "")
    qs = Property.objects.select_related("contact", "sourced_by").order_by("-updated_at")
    if status in Property.Status.values:
        qs = qs.filter(status=status)
    pending = Verification.objects.filter(decision=Verification.Decision.PENDING).select_related(
        "property", "verified_by"
    )
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "ops/property_list.html",
        {"page": page, "status": status, "statuses": Property.Status.choices, "pending": pending},
    )


@role_required(Role.ACQUISITION)
@require_http_methods(["GET", "POST"])
def contact_new(request: HttpRequest) -> HttpResponse:
    form = ContactForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        contact = _attempt(
            request,
            form,
            lambda: listings.create_contact(actor=request.user, **form.cleaned_data),
            "Contact saved.",
        )
        if contact is not FAILED:
            return redirect("ops:property_new")
    return render(request, "ops/form.html", {"form": form, "title": "New property contact"})


@role_required(Role.ACQUISITION)
@require_http_methods(["GET", "POST"])
def property_new(request: HttpRequest) -> HttpResponse:
    form = NewPropertyForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        prop = _attempt(
            request,
            form,
            lambda: listings.create_property(
                actor=request.user,
                contact=form.cleaned_data["contact"],
                details=form.details(),
                authority_document=form.cleaned_data["authority_document"],
            ),
            "Draft listing created. Add photos, then a verifier inspects it.",
        )
        if prop is not FAILED:
            return redirect("ops:property_detail", pk=prop.pk)
    return render(
        request, "ops/form.html", {"form": form, "title": "New property", "multipart": True}
    )


@role_required(Role.ACQUISITION)
@require_http_methods(["GET", "POST"])
def property_edit(request: HttpRequest, pk: str) -> HttpResponse:
    prop = get_object_or_404(Property, pk=pk)
    form = PropertyForm(request.POST or None, initial=PropertyForm.initial_from(prop))
    if request.method == "POST" and form.is_valid():
        updated = _attempt(
            request,
            form,
            lambda: listings.update_property(actor=request.user, prop=prop, details=form.details()),
            "Listing updated.",
        )
        if updated is not FAILED:
            return redirect("ops:property_detail", pk=pk)
    return render(request, "ops/form.html", {"form": form, "title": f"Edit {prop.title}"})


@role_required(*LISTING_ROLES)
@require_http_methods(["GET", "POST"])
def property_detail(request: HttpRequest, pk: str) -> HttpResponse:
    prop = get_object_or_404(
        Property.objects.select_related("contact", "sourced_by").prefetch_related("photos"), pk=pk
    )
    pending = prop.verifications.filter(decision=Verification.Decision.PENDING).first()
    action = request.POST.get("form") if request.method == "POST" else None
    photo_form = (
        PhotoForm(request.POST or None, request.FILES or None) if action == "photo" else PhotoForm()
    )
    verify_form = (
        VerificationForm(request.POST or None) if action == "verify" else VerificationForm()
    )
    decision_form = DecisionForm(request.POST or None) if action == "decide" else DecisionForm()
    availability_form = (
        AvailabilityForm(request.POST or None) if action == "availability" else AvailabilityForm()
    )

    done = False
    if action == "photo" and photo_form.is_valid():
        done = (
            _attempt(
                request,
                photo_form,
                lambda: listings.add_photo(
                    actor=request.user,
                    prop=prop,
                    image=photo_form.cleaned_data["image"],
                    caption=photo_form.cleaned_data["caption"],
                ),
                "Photo added.",
            )
            is not FAILED
        )
    elif action == "verify" and verify_form.is_valid():
        done = (
            _attempt(
                request,
                verify_form,
                lambda: listings.submit_verification(
                    actor=request.user,
                    prop=prop,
                    checklist=verify_form.checklist(),
                    checked_at=verify_form.cleaned_data["checked_at"],
                    notes=verify_form.cleaned_data["notes"],
                ),
                "Inspection submitted for approval.",
            )
            is not FAILED
        )
    elif action == "decide" and decision_form.is_valid() and pending is not None:
        done = (
            _attempt(
                request,
                decision_form,
                lambda: listings.decide_verification(
                    actor=request.user,
                    verification=pending,
                    approve=decision_form.cleaned_data["decision"] == "approve",
                    note=decision_form.cleaned_data["note"],
                ),
                "Decision saved.",
            )
            is not FAILED
        )
    elif action == "availability" and availability_form.is_valid():
        done = (
            _attempt(
                request,
                availability_form,
                lambda: listings.change_availability(
                    actor=request.user, prop=prop, **availability_form.cleaned_data
                ),
                "Availability updated.",
            )
            is not FAILED
        )
    if done:
        return redirect("ops:property_detail", pk=pk)

    user = request.user
    return render(
        request,
        "ops/property_detail.html",
        {
            "property": prop,
            "pending": pending,
            "history": prop.verifications.exclude(pk=getattr(pending, "pk", None)),
            "photo_form": photo_form,
            "verify_form": verify_form,
            "decision_form": decision_form,
            "availability_form": availability_form,
            "can_edit": has_role(user, Role.ACQUISITION),
            "can_photo": has_role(user, Role.ACQUISITION) or has_role(user, Role.VERIFICATION),
            "can_verify": has_role(user, Role.VERIFICATION),
            "can_decide": pending is not None
            and has_role(user, Role.APPROVAL)
            and user.pk not in {pending.verified_by_id, prop.sourced_by_id},
        },
    )


@never_cache
@role_required(Role.VERIFICATION, Role.APPROVAL)
@require_GET
def authority_document(request: HttpRequest, pk: str) -> FileResponse:
    prop = get_object_or_404(Property, pk=pk)
    if not prop.authority_document:
        raise Http404
    record(actor=request.user, action="property.authority_viewed", target=prop)
    response = FileResponse(prop.authority_document.open("rb"), content_type="image/jpeg")
    response["Cache-Control"] = "no-store, private"
    return response


# --- Booking desk ---------------------------------------------------------------


@role_required(Role.BOOKINGS)
@require_GET
def booking_queue(request: HttpRequest) -> HttpResponse:
    S = Appointment.Status
    base = Appointment.objects.select_related("property", "customer", "broker__user")
    return render(
        request,
        "ops/booking_queue.html",
        {
            "requested": base.filter(status=S.REQUESTED).order_by("created_at"),
            "awaiting_payment": base.filter(status=S.AWAITING_PAYMENT).order_by("payment_due_at"),
            "booked": base.filter(status=S.BOOKED).order_by("scheduled_start"),
        },
    )


@role_required(Role.BOOKINGS)
@require_http_methods(["GET", "POST"])
def booking_detail(request: HttpRequest, pk: str) -> HttpResponse:
    appointment = get_object_or_404(
        Appointment.objects.select_related("property__contact", "customer", "broker__user"), pk=pk
    )
    action = request.POST.get("form") if request.method == "POST" else None
    initial = {"scheduled_start": appointment.scheduled_start or appointment.preferred_start}
    if appointment.broker_id:
        initial["broker"] = appointment.broker_id
    confirm_form = (
        ConfirmForm(request.POST, initial=initial)
        if action in {"confirm", "reschedule"}
        else ConfirmForm(initial=initial)
    )
    close_form = CloseForm(request.POST) if action in {"decline", "cancel"} else CloseForm()

    done = False
    if action in {"confirm", "reschedule"} and confirm_form.is_valid():
        service = bookings.confirm if action == "confirm" else bookings.reschedule
        done = (
            _attempt(
                request,
                confirm_form,
                lambda: service(
                    actor=request.user, appointment=appointment, **confirm_form.cleaned_data
                ),
                "Saved. The customer has been texted.",
            )
            is not FAILED
        )
    elif action in {"decline", "cancel"} and close_form.is_valid():
        service = bookings.decline if action == "decline" else bookings.cancel_by_staff
        done = (
            _attempt(
                request,
                close_form,
                lambda: service(
                    actor=request.user, appointment=appointment, **close_form.cleaned_data
                ),
                "Saved. The customer has been texted.",
            )
            is not FAILED
        )
    if done:
        return redirect("ops:booking_detail", pk=pk)

    return render(
        request,
        "ops/booking_detail.html",
        {
            "appointment": appointment,
            "confirm_form": confirm_form,
            "close_form": close_form,
            "payments": appointment.payments.all(),
            "report": getattr(appointment, "report", None),
            "feedback": getattr(appointment, "feedback", None),
        },
    )


# --- Brokers --------------------------------------------------------------------


@role_required(Role.BOOKINGS)
@require_http_methods(["GET", "POST"])
def broker_list(request: HttpRequest) -> HttpResponse:
    form = BrokerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        onboarded = _attempt(
            request,
            form,
            lambda: brokers.onboard_broker(
                actor=request.user,
                phone=data["phone"],
                full_name=data["full_name"],
                id_checked_at=data["id_checked_at"],
                terms_signed_at=data["terms_signed_at"],
                areas=data["areas"],
            ),
            "Broker onboarded. They sign in with their phone number.",
        )
        if onboarded is not FAILED:
            return redirect("ops:broker_list")
    return render(
        request,
        "ops/broker_list.html",
        {
            "brokers": BrokerProfile.objects.select_related("user").order_by(
                "status", "created_at"
            ),
            "form": form,
            "status_form": BrokerStatusForm(),
        },
    )


@role_required(Role.BOOKINGS)
@require_POST
def broker_status(request: HttpRequest, pk: str) -> HttpResponse:
    broker = get_object_or_404(BrokerProfile, pk=pk)
    form = BrokerStatusForm(request.POST)
    if form.is_valid():
        _attempt(
            request,
            None,
            lambda: brokers.set_broker_status(
                actor=request.user, broker=broker, **form.cleaned_data
            ),
            "Broker status updated.",
        )
    else:
        messages.error(request, "Choose a status and give a reason.")
    return redirect("ops:broker_list")


# --- Finance --------------------------------------------------------------------


@role_required(Role.FINANCE)
@require_GET
def finance(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "ops/finance.html",
        {
            "awaiting": Appointment.objects.filter(status=Appointment.Status.AWAITING_PAYMENT)
            .select_related("customer", "property")
            .order_by("payment_due_at"),
            "mismatches": Payment.objects.filter(status=Payment.Status.MISMATCH).select_related(
                "appointment"
            ),
            "refunds": Refund.objects.filter(status=Refund.Status.REQUESTED).select_related(
                "payment__appointment", "requested_by"
            ),
            "unreconciled": Payment.objects.filter(
                status=Payment.Status.SUCCEEDED, reconciled_at__isnull=True
            ).select_related("appointment", "recorded_by"),
            "payment_form": ManualPaymentForm(),
            "payout_form": PayoutForm(),
            "reconcile_form": ReconcileForm(),
            "resolve_form": ResolveForm(),
        },
    )


@role_required(Role.FINANCE)
@require_POST
def record_payment(request: HttpRequest) -> HttpResponse:
    form = ManualPaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter the booking reference, transaction ID and amount.")
        return redirect("ops:finance")
    appointment = Appointment.objects.filter(reference=form.cleaned_data["reference"]).first()
    if appointment is None:
        messages.error(request, "No viewing has that reference.")
        return redirect("ops:finance")
    payment = _attempt(
        request,
        None,
        lambda: payments.record_manual_payment(
            actor=request.user,
            appointment=appointment,
            provider_txn_id=form.cleaned_data["provider_txn_id"],
            amount_ugx=form.cleaned_data["amount_ugx"],
            amount_differs=form.cleaned_data["amount_differs"],
        ),
        f"Recorded for {appointment.reference}.",
    )
    if payment is not FAILED and payment.status == Payment.Status.MISMATCH:
        messages.warning(
            request, "The amount differs from the fee, so it is held for a second person to settle."
        )
    return redirect("ops:finance")


@role_required(Role.FINANCE)
@require_POST
def resolve_payment(request: HttpRequest, pk: str) -> HttpResponse:
    payment = get_object_or_404(Payment, pk=pk)
    form = ResolveForm(request.POST)
    if form.is_valid():
        _attempt(
            request,
            None,
            lambda: payments.resolve_mismatch(
                actor=request.user,
                payment=payment,
                accept=form.cleaned_data["decision"] == "accept",
                note=form.cleaned_data["note"],
            ),
            "Mismatch settled.",
        )
    else:
        messages.error(request, "Choose a decision and say why.")
    return redirect("ops:finance")


@role_required(Role.FINANCE)
@require_POST
def pay_refund(request: HttpRequest, pk: str) -> HttpResponse:
    refund = get_object_or_404(Refund, pk=pk)
    form = PayoutForm(request.POST)
    if form.is_valid():
        _attempt(
            request,
            None,
            lambda: payments.mark_refund_paid(
                actor=request.user, refund=refund, payout_ref=form.cleaned_data["payout_ref"]
            ),
            "Refund marked as paid. The customer has been texted.",
        )
    else:
        messages.error(request, "Enter the payout reference.")
    return redirect("ops:finance")


@role_required(Role.FINANCE)
@require_POST
def reconcile_payment(request: HttpRequest, pk: str) -> HttpResponse:
    payment = get_object_or_404(Payment, pk=pk)
    form = ReconcileForm(request.POST)
    if form.is_valid():
        _attempt(
            request,
            None,
            lambda: payments.reconcile(
                actor=request.user,
                payment=payment,
                statement_ref=form.cleaned_data["statement_ref"],
            ),
            "Payment reconciled.",
        )
    else:
        messages.error(request, "Enter the statement reference.")
    return redirect("ops:finance")
