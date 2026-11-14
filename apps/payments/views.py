import json
import logging
import secrets
import time

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from . import services
from .models import Payment
from .providers import SIGNATURE_HEADER, WebhookError, sign

logger = logging.getLogger(__name__)
MAX_WEBHOOK_BYTES = 64 * 1024


@csrf_exempt  # authenticated by the provider's HMAC signature instead
@require_POST
def webhook(request: HttpRequest) -> HttpResponse:
    if len(request.body) > MAX_WEBHOOK_BYTES:
        return HttpResponse(status=413)
    try:
        outcome = services.handle_webhook(body=request.body, meta=request.META)
    except WebhookError:
        logger.warning("Rejected payment webhook")
        return HttpResponse(status=400)
    return HttpResponse(outcome, content_type="text/plain")


@login_required
@require_http_methods(["GET", "POST"])
def dev_checkout(request: HttpRequest, reference: str) -> HttpResponse:
    """Simulated provider page. Exists only with PAYMENT_PROVIDER=dev (refused in prod)."""
    if settings.PAYMENT_PROVIDER != "dev":
        raise Http404
    payment = get_object_or_404(
        Payment.objects.select_related("appointment"),
        reference=reference,
        appointment__customer=request.user,
    )
    if request.method == "POST":
        outcome = "succeeded" if request.POST.get("result") == "succeed" else "failed"
        body = json.dumps(
            {
                "id": secrets.token_hex(8),
                "reference": payment.reference,
                "status": outcome,
                "amount": payment.amount_ugx,
                "currency": "UGX",
                "transaction_id": "DEV" + secrets.token_hex(6).upper(),
            }
        ).encode()
        timestamp = int(time.time())
        signature = f"t={timestamp},v1={sign(settings.PAYMENT_WEBHOOK_SECRET, timestamp, body)}"
        services.handle_webhook(body=body, meta={SIGNATURE_HEADER: signature})
        return redirect("bookings:detail", pk=payment.appointment_id)
    return render(request, "payments/dev_checkout.html", {"payment": payment})
