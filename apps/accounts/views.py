from django.conf import settings
from django.contrib.auth import login, logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.net import get_client_ip
from apps.core.phone import mask_phone

from . import services
from .forms import CodeForm, PhoneForm

SESSION_PHONE = "signin_phone"
SESSION_NEXT = "signin_next"


def _safe_next(request: HttpRequest, candidate: str | None) -> str:
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return ""


@require_http_methods(["GET", "POST"])
def phone_step(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)

    form = PhoneForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        phone = form.cleaned_data["phone"]
        try:
            services.request_login_code(phone, client_ip=get_client_ip(request))
        except services.SignInError as exc:
            form.add_error(None, str(exc))
        else:
            request.session[SESSION_PHONE] = phone
            request.session[SESSION_NEXT] = _safe_next(request, request.GET.get("next"))
            return redirect("accounts:code")

    return render(request, "accounts/phone_step.html", {"form": form})


@require_http_methods(["GET", "POST"])
def code_step(request: HttpRequest) -> HttpResponse:
    phone = request.session.get(SESSION_PHONE)
    if not phone:
        return redirect("accounts:login")

    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = services.complete_login(phone, form.cleaned_data["code"])
        except services.SignInError as exc:
            form.add_error("code", str(exc))
        else:
            next_url = request.session.pop(SESSION_NEXT, "") or settings.LOGIN_REDIRECT_URL
            request.session.pop(SESSION_PHONE, None)
            # login() rotates the session key, preventing session fixation.
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect(next_url)

    return render(
        request, "accounts/code_step.html", {"form": form, "masked_phone": mask_phone(phone)}
    )


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)
