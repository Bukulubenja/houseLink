import re
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import OneTimeCode, User

pytestmark = pytest.mark.django_db(transaction=True)  # so on_commit SMS hooks fire


def _sent_code(sms_outbox) -> str:
    return re.search(r"\b(\d{6})\b", sms_outbox[-1].body).group(1)


def _clear_cooldown(phone: str) -> None:
    cache.delete(f"cd:otp:{phone}")


def test_request_sends_sms_and_stores_only_a_hash(phone, sms_outbox):
    services.request_login_code(phone, client_ip="41.0.0.1")

    assert len(sms_outbox) == 1 and sms_outbox[0].to == phone
    code = _sent_code(sms_outbox)
    otp = OneTimeCode.objects.get(phone=phone)
    assert code not in otp.code_hash
    assert otp.purpose == OneTimeCode.Purpose.LOGIN
    assert otp.is_usable


def test_correct_code_creates_verified_user(phone, sms_outbox):
    services.request_login_code(phone)
    user = services.complete_login(phone, _sent_code(sms_outbox))

    assert user.phone == phone
    assert user.phone_verified_at is not None
    assert OneTimeCode.objects.get(phone=phone).consumed_at is not None


def test_existing_user_is_returned_not_duplicated(phone, sms_outbox):
    existing = User.objects.create_user(phone)
    services.request_login_code(phone)
    assert services.complete_login(phone, _sent_code(sms_outbox)) == existing
    assert User.objects.count() == 1


def test_code_cannot_be_reused(phone, sms_outbox):
    services.request_login_code(phone)
    code = _sent_code(sms_outbox)
    services.complete_login(phone, code)
    with pytest.raises(services.SignInError):
        services.complete_login(phone, code)


def test_wrong_code_counts_attempt_and_fails(phone, sms_outbox):
    services.request_login_code(phone)
    with pytest.raises(services.SignInError):
        services.complete_login(phone, "000000" if _sent_code(sms_outbox) != "000000" else "111111")
    assert OneTimeCode.objects.get(phone=phone).attempts == 1
    assert not User.objects.exists()


def test_code_locks_after_max_attempts_even_if_then_correct(phone, sms_outbox, settings):
    services.request_login_code(phone)
    code = _sent_code(sms_outbox)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(settings.OTP_MAX_ATTEMPTS):
        with pytest.raises(services.SignInError):
            services.complete_login(phone, wrong)
    with pytest.raises(services.SignInError):
        services.complete_login(phone, code)


def test_expired_code_is_rejected(phone, sms_outbox):
    services.request_login_code(phone)
    OneTimeCode.objects.filter(phone=phone).update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(services.SignInError):
        services.complete_login(phone, _sent_code(sms_outbox))


def test_new_code_cancels_the_previous_one(phone, sms_outbox):
    services.request_login_code(phone)
    first = _sent_code(sms_outbox)
    _clear_cooldown(phone)
    services.request_login_code(phone)
    second = _sent_code(sms_outbox)

    if first != second:
        with pytest.raises(services.SignInError):
            services.complete_login(phone, first)
    assert services.complete_login(phone, second).phone == phone


def test_code_for_one_phone_does_not_work_for_another(phone, sms_outbox):
    services.request_login_code(phone)
    code = _sent_code(sms_outbox)
    with pytest.raises(services.SignInError):
        services.complete_login("+256701999888", code)


def test_resend_cooldown(phone):
    services.request_login_code(phone)
    with pytest.raises(services.RateLimitedError, match="wait a minute"):
        services.request_login_code(phone)


def test_per_phone_hourly_limit(phone, settings):
    settings.OTP_MAX_PER_PHONE_PER_HOUR = 2
    for _ in range(2):
        services.request_login_code(phone)
        _clear_cooldown(phone)
    with pytest.raises(services.RateLimitedError, match="this number"):
        services.request_login_code(phone)


def test_per_ip_hourly_limit(settings):
    settings.OTP_MAX_PER_IP_PER_HOUR = 2
    services.request_login_code("+256772000001", client_ip="41.0.0.9")
    services.request_login_code("+256772000002", client_ip="41.0.0.9")
    with pytest.raises(services.RateLimitedError, match="network"):
        services.request_login_code("+256772000003", client_ip="41.0.0.9")


def test_suspended_user_cannot_sign_in(phone, sms_outbox):
    User.objects.create_user(phone, is_active=False)
    services.request_login_code(phone)
    with pytest.raises(services.SignInError, match="suspended"):
        services.complete_login(phone, _sent_code(sms_outbox))


def test_staff_cannot_sign_in_by_sms(phone, sms_outbox):
    User.objects.create_user(phone, password="a-long-admin-password", is_staff=True)
    services.request_login_code(phone)
    with pytest.raises(services.SignInError, match="admin login"):
        services.complete_login(phone, _sent_code(sms_outbox))


def test_no_code_requested_fails_cleanly(phone):
    with pytest.raises(services.SignInError):
        services.complete_login(phone, "123456")
