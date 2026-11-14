"""Customer pages (request, track, pay, cancel, feedback) and broker pages (my viewings,
check in, report). Customers see only their own appointments; brokers only those assigned
to them, and a customer's phone and the directions only once the viewing is paid."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.brokers.services import active_broker_for
from apps.listings.models import Property
from apps.payments import services as payments

from . import services
from .forms import FeedbackForm, ReportForm, RequestForm
from .models import Appointment

Status = Appointment.Status


def _own_appointment(request: HttpRequest, pk: str) -> Appointment:
    return get_object_or_404(
        Appointment.objects.select_related("property", "broker__user"),
        pk=pk,
        customer=request.user,
    )


@login_required
@require_http_methods(["GET", "POST"])
def request_viewing(request: HttpRequest, property_pk: str) -> HttpResponse:
    prop = get_object_or_404(Property, pk=property_pk)
    if not prop.is_bookable:
        raise Http404
    form = RequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            appointment = services.request_viewing(
                customer=request.user,
                prop=prop,
                preferred_start=form.cleaned_data["preferred_start"],
                note=form.cleaned_data["note"],
            )
        except services.BookingError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Request sent. We'll text you once the time is confirmed.")
            return redirect("bookings:detail", pk=appointment.pk)
    return render(
        request,
        "bookings/request.html",
        {"form": form, "property": prop, "fee": settings.VIEWING_FEE_UGX},
    )


@login_required
@require_GET
def my_viewings(request: HttpRequest) -> HttpResponse:
    appointments = Appointment.objects.filter(customer=request.user).select_related("property")
    return render(request, "bookings/list.html", {"appointments": appointments})


@login_required
@require_GET
def detail(request: HttpRequest, pk: str) -> HttpResponse:
    appointment = _own_appointment(request, pk)
    context = {
        "appointment": appointment,
        "instructions": payments.payment_instructions(appointment),
        "has_checkout": payments.get_provider().has_checkout,
        "refund_on_cancel": services.refund_due_on_customer_cancel(appointment),
        "cutoff_hours": settings.CANCELLATION_REFUND_CUTOFF_HOURS,
        "support_phone": settings.SUPPORT_PHONE,
        "feedback_form": None,
    }
    if appointment.status == Status.COMPLETED and not hasattr(appointment, "feedback"):
        context["feedback_form"] = FeedbackForm()
    return render(request, "bookings/detail.html", context)


@login_required
@require_POST
def pay(request: HttpRequest, pk: str) -> HttpResponse:
    appointment = _own_appointment(request, pk)
    try:
        checkout = payments.start_checkout(customer=request.user, appointment=appointment)
    except payments.PaymentError as exc:
        messages.error(request, str(exc))
        return redirect("bookings:detail", pk=pk)
    if checkout.redirect_url:
        return redirect(checkout.redirect_url)
    messages.info(request, checkout.instructions)
    return redirect("bookings:detail", pk=pk)


@login_required
@require_POST
def cancel(request: HttpRequest, pk: str) -> HttpResponse:
    appointment = _own_appointment(request, pk)
    try:
        services.cancel_by_customer(customer=request.user, appointment=appointment)
    except services.BookingError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Viewing cancelled.")
    return redirect("bookings:detail", pk=pk)


@login_required
@require_POST
def feedback(request: HttpRequest, pk: str) -> HttpResponse:
    appointment = _own_appointment(request, pk)
    form = FeedbackForm(request.POST)
    if form.is_valid():
        try:
            services.leave_feedback(
                customer=request.user,
                appointment=appointment,
                rating=form.cleaned_data["rating"],
                comment=form.cleaned_data["comment"],
                may_publish=form.cleaned_data["may_publish"],
            )
        except services.BookingError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Thank you for your feedback.")
    else:
        messages.error(request, "Choose a rating from 1 to 5.")
    return redirect("bookings:detail", pk=pk)


# --- Broker ---------------------------------------------------------------------


def _broker_or_404(request: HttpRequest):
    broker = active_broker_for(request.user)
    if broker is None:
        raise Http404
    return broker


@login_required
@require_GET
def broker_home(request: HttpRequest) -> HttpResponse:
    broker = _broker_or_404(request)
    since = timezone.now() - services.CHECK_IN_LATE
    upcoming = (
        Appointment.objects.filter(broker=broker, status=Status.BOOKED, scheduled_start__gte=since)
        .select_related("property")
        .order_by("scheduled_start")
    )
    pending_payment = Appointment.objects.filter(
        broker=broker, status=Status.AWAITING_PAYMENT
    ).select_related("property")
    return render(
        request,
        "bookings/broker_home.html",
        {"broker": broker, "upcoming": upcoming, "pending_payment": pending_payment},
    )


@login_required
@require_http_methods(["GET", "POST"])
def broker_appointment(request: HttpRequest, pk: str) -> HttpResponse:
    broker = _broker_or_404(request)
    appointment = get_object_or_404(
        Appointment.objects.select_related("property__contact", "customer"),
        pk=pk,
        broker=broker,
        status=Status.BOOKED,
    )
    form = ReportForm(request.POST or None)
    if request.method == "POST":
        try:
            if request.POST.get("action") == "check_in":
                services.check_in(user=request.user, appointment=appointment)
                messages.success(request, "Checked in.")
                return redirect("broker:appointment", pk=pk)
            if form.is_valid():
                services.submit_report(
                    user=request.user, appointment=appointment, **form.cleaned_data
                )
                messages.success(request, "Report submitted. Thank you.")
                return redirect("broker:home")
        except services.BookingError as exc:
            form.add_error(None, str(exc))
        except PermissionDenied as exc:
            raise Http404 from exc
    return render(
        request, "bookings/broker_appointment.html", {"appointment": appointment, "form": form}
    )
