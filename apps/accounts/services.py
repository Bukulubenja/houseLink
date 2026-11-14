"""Phone sign-in. Sign-up and sign-in are one flow, so responses never reveal who is registered."""

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from apps.core import ratelimit
from apps.core.phone import mask_phone
from apps.core.tasks import send_sms_task

from .models import OneTimeCode, User

logger = logging.getLogger(__name__)

ONE_HOUR = 60 * 60
INVALID_CODE_MESSAGE = "That code is wrong or has expired. Request a new one."


class SignInError(Exception):
    """A user-facing failure; str(error) is safe to show."""


class RateLimitedError(SignInError):
    pass


def _hash_code(phone: str, purpose: str, code: str) -> str:
    return salted_hmac(
        "accounts.one-time-code", f"{purpose}:{phone}:{code}", algorithm="sha256"
    ).hexdigest()


def _new_code() -> str:
    return f"{secrets.randbelow(10**settings.OTP_LENGTH):0{settings.OTP_LENGTH}d}"


def _check_send_limits(phone: str, client_ip: str) -> None:
    if not ratelimit.claim_cooldown(f"otp:{phone}", settings.OTP_RESEND_COOLDOWN_SECONDS):
        raise RateLimitedError("Please wait a minute before requesting another code.")
    if not ratelimit.allow(f"otp:phone:{phone}", settings.OTP_MAX_PER_PHONE_PER_HOUR, ONE_HOUR):
        raise RateLimitedError("Too many codes requested for this number. Try again later.")
    if client_ip and not ratelimit.allow(
        f"otp:ip:{client_ip}", settings.OTP_MAX_PER_IP_PER_HOUR, ONE_HOUR
    ):
        raise RateLimitedError("Too many requests from your network. Try again later.")


def issue_code(phone: str, purpose: str, client_ip: str = "") -> None:
    """Create a fresh code for `phone` (already normalised), cancel older ones, and SMS it."""
    _check_send_limits(phone, client_ip)
    now = timezone.now()
    code = _new_code()

    with transaction.atomic():
        OneTimeCode.objects.filter(phone=phone, purpose=purpose, consumed_at__isnull=True).update(
            expires_at=now
        )
        OneTimeCode.objects.create(
            phone=phone,
            purpose=purpose,
            code_hash=_hash_code(phone, purpose, code),
            expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
        )
        body = f"Your Crib Link code is {code}. It expires in 5 minutes. Never share it."
        # argsrepr keeps the code out of worker logs and monitoring tools (Flower).
        transaction.on_commit(
            lambda: send_sms_task.apply_async(
                args=(phone, body), argsrepr=f"({mask_phone(phone)!r}, '<redacted>')"
            )
        )

    logger.info("Issued %s code to %s", purpose, mask_phone(phone))


def check_code(phone: str, purpose: str, code: str) -> None:
    """Consume a matching code or raise SignInError. Wrong guesses are counted and persisted."""
    with transaction.atomic():
        otp = (
            OneTimeCode.objects.select_for_update()
            .filter(phone=phone, purpose=purpose, consumed_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if otp is None or not otp.is_usable or otp.attempts >= settings.OTP_MAX_ATTEMPTS:
            raise SignInError(INVALID_CODE_MESSAGE)

        matches = constant_time_compare(otp.code_hash, _hash_code(phone, purpose, code.strip()))
        if matches:
            otp.consumed_at = timezone.now()
        else:
            otp.attempts += 1
        otp.save(update_fields=["consumed_at", "attempts"])

    if not matches:
        logger.info("Wrong %s code for %s (attempt %s)", purpose, mask_phone(phone), otp.attempts)
        raise SignInError(INVALID_CODE_MESSAGE)


def request_login_code(phone: str, client_ip: str = "") -> None:
    issue_code(phone, OneTimeCode.Purpose.LOGIN, client_ip)


def complete_login(phone: str, code: str) -> User:
    """Verify the code and return the (possibly new) user. Suspended users are refused."""
    check_code(phone, OneTimeCode.Purpose.LOGIN, code)

    user = User.objects.filter(phone=phone).first() or User.objects.create_user(phone)
    if not user.is_active:
        raise SignInError("This account is suspended. Contact Crib Link support.")
    if user.is_staff:
        # Staff powers (ID review, bans) must not be one SIM swap away.
        raise SignInError("Staff accounts sign in with their password at the admin login.")
    if user.phone_verified_at is None:
        user.phone_verified_at = timezone.now()
        user.save(update_fields=["phone_verified_at"])
    return user
