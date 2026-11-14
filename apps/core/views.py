import logging

from django.db import DatabaseError, connection, transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@require_GET
def home(request: HttpRequest) -> HttpResponse:
    return render(request, "core/home.html")


@transaction.non_atomic_requests  # must answer 503 even when a transaction can't open
@never_cache
@require_GET
def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness + database check for the load balancer. Reveals no internals."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        logger.exception("Health check: database unavailable")
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
