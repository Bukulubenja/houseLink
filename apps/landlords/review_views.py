"""Super-admin review queue for landlord identity checks."""

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from apps.core.decorators import superuser_required

from . import services
from .forms import ReviewForm
from .models import IdentitySubmission

QUEUE_PAGE_SIZE = 25


@superuser_required
@require_GET
def identity_queue(request: HttpRequest) -> HttpResponse:
    pending = IdentitySubmission.objects.filter(
        status=IdentitySubmission.Status.PENDING
    ).select_related("profile__user")
    page = Paginator(pending, QUEUE_PAGE_SIZE).get_page(request.GET.get("page"))
    overdue_before = timezone.now() - timedelta(hours=settings.IDENTITY_REVIEW_TARGET_HOURS)
    return render(
        request,
        "review/identity_queue.html",
        {"page": page, "overdue_before": overdue_before},
    )


@superuser_required
@require_http_methods(["GET", "POST"])
def identity_detail(request: HttpRequest, pk: str) -> HttpResponse:
    submission = get_object_or_404(
        IdentitySubmission.objects.select_related("profile__user", "reviewed_by"), pk=pk
    )
    form = ReviewForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.review_identity(
                reviewer=request.user,
                submission=submission,
                decision=form.cleaned_data["decision"],
                reason=form.cleaned_data["reason"],
                note=form.cleaned_data["note"],
            )
        except services.IdentityError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Decision saved for {submission.legal_name}.")
            return redirect("review:identity_queue")

    history = submission.profile.submissions.exclude(pk=submission.pk).order_by("-created_at")
    return render(
        request,
        "review/identity_detail.html",
        {"submission": submission, "form": form, "history": history},
    )


@never_cache
@superuser_required
@require_GET
def identity_document(request: HttpRequest, pk: str, kind: str) -> FileResponse:
    """Stream an ID image through Django so no reusable signed URL ever reaches the browser."""
    submission = get_object_or_404(IdentitySubmission.objects.select_related("profile"), pk=pk)
    try:
        document = services.open_document(viewer=request.user, submission=submission, kind=kind)
    except services.IdentityError as exc:
        raise Http404 from exc
    response = FileResponse(document.open("rb"), content_type="image/jpeg")
    response["Cache-Control"] = "no-store, private"
    response["Content-Disposition"] = "inline"
    return response
