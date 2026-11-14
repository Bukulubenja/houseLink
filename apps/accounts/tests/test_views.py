import re

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import OneTimeCode, User

pytestmark = pytest.mark.django_db(transaction=True)

LOGIN = reverse("accounts:login")
CODE = reverse("accounts:code")
LOGOUT = reverse("accounts:logout")


def _code(sms_outbox) -> str:
    return re.search(r"\b(\d{6})\b", sms_outbox[-1].body).group(1)


def test_full_sign_in_flow(client, sms_outbox):
    response = client.post(LOGIN, {"phone": "0772 123 456"})
    assert response.status_code == 302 and response.url == CODE

    page = client.get(CODE)
    assert b"+256 77*** *456" in page.content  # number is masked on screen

    response = client.post(CODE, {"code": _code(sms_outbox)})
    assert response.status_code == 302 and response.url == reverse("core:home")
    assert client.session["_auth_user_id"] == str(User.objects.get().pk)
    assert "signin_phone" not in client.session


def test_session_key_rotates_on_sign_in(client, sms_outbox):
    client.post(LOGIN, {"phone": "0772123456"})
    before = client.session.session_key
    client.post(CODE, {"code": _code(sms_outbox)})
    assert client.session.session_key != before


def test_invalid_phone_shows_error(client, sms_outbox):
    response = client.post(LOGIN, {"phone": "12"})
    assert response.status_code == 200
    assert b"valid phone number" in response.content
    assert sms_outbox == []


def test_rate_limit_error_is_shown_on_form(client):
    client.post(LOGIN, {"phone": "0772123456"})
    response = client.post(LOGIN, {"phone": "0772123456"})
    assert response.status_code == 200
    assert b"wait a minute" in response.content


def test_wrong_code_shows_error_and_does_not_log_in(client, sms_outbox):
    client.post(LOGIN, {"phone": "0772123456"})
    wrong = "000000" if _code(sms_outbox) != "000000" else "111111"
    response = client.post(CODE, {"code": wrong})
    assert response.status_code == 200
    assert b"wrong or has expired" in response.content
    assert "_auth_user_id" not in client.session


def test_failed_attempts_persist_across_requests(client, sms_outbox, settings):
    """ATOMIC_REQUESTS must not roll back the attempt counter, or guessing is unlimited."""
    client.post(LOGIN, {"phone": "0772123456"})
    code = _code(sms_outbox)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(settings.OTP_MAX_ATTEMPTS):
        client.post(CODE, {"code": wrong})
    assert OneTimeCode.objects.get().attempts == settings.OTP_MAX_ATTEMPTS
    response = client.post(CODE, {"code": code})  # right code, but locked out
    assert "_auth_user_id" not in client.session
    assert b"wrong or has expired" in response.content


def test_malformed_code_is_rejected_by_form(client, sms_outbox):
    client.post(LOGIN, {"phone": "0772123456"})
    response = client.post(CODE, {"code": "12ab"})
    assert b"6-digit code" in response.content


def test_code_step_without_phone_redirects_to_start(client):
    response = client.get(CODE)
    assert response.status_code == 302 and response.url == LOGIN


def test_safe_next_is_followed(client, sms_outbox):
    client.post(f"{LOGIN}?next=/listings/abc/", {"phone": "0772123456"})
    response = client.post(CODE, {"code": _code(sms_outbox)})
    assert response.url == "/listings/abc/"


@pytest.mark.parametrize(
    "evil", ["https://evil.example/", "//evil.example/", "javascript:alert(1)"]
)
def test_open_redirect_is_blocked(client, sms_outbox, evil):
    client.post(f"{LOGIN}?next={evil}", {"phone": "0772123456"})
    response = client.post(CODE, {"code": _code(sms_outbox)})
    assert response.url == reverse("core:home")


def test_signed_in_user_skips_login_page(client):
    client.force_login(User.objects.create_user("+256772123456"))
    response = client.get(LOGIN)
    assert response.status_code == 302


def test_logout_requires_post(client):
    client.force_login(User.objects.create_user("+256772123456"))
    assert client.get(LOGOUT).status_code == 405
    response = client.post(LOGOUT)
    assert response.status_code == 302
    assert "_auth_user_id" not in client.session


def test_csrf_is_enforced_on_sign_in(sms_outbox):
    strict = Client(enforce_csrf_checks=True)
    response = strict.post(LOGIN, {"phone": "0772123456"})
    assert response.status_code == 403
    assert sms_outbox == []


def test_regular_users_cannot_use_admin(client):
    client.force_login(User.objects.create_user("+256772123456"))
    response = client.get(reverse("admin:index"))
    assert response.status_code == 302 and "login" in response.url
