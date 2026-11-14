from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from . import services
from .forms import IdentityForm
from .models import LandlordProfile


def _request_too_large(request: HttpRequest) -> bool:
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return True
    return length > settings.IDENTITY_REQUEST_MAX_BYTES


@login_required
@require_http_methods(["GET", "POST"])
def verify_identity(request: HttpRequest) -> HttpResponse:
    profile = LandlordProfile.objects.filter(user=request.user).first()
    latest = profile.submissions.order_by("-created_at").first() if profile else None

    if profile and not profile.can_submit_identity:
        return render(
            request, "landlords/verify_status.html", {"profile": profile, "latest": latest}
        )

    if request.method == "POST" and _request_too_large(request):
        return HttpResponse("Upload too large. Use smaller photos.", status=413)

    form = IdentityForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            services.submit_identity(
                user=request.user,
                legal_name=data["legal_name"],
                nin=data["nin"],
                id_front=data["id_front"],
                id_back=data["id_back"],
                selfie=data["selfie"],
            )
        except services.IdentityError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Thanks — we'll review your ID, usually within 24 hours.")
            return redirect("landlords:verify")

    return render(
        request, "landlords/verify_form.html", {"form": form, "profile": profile, "latest": latest}
    )
