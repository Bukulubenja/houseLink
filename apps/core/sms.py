"""SMS gateway. Callers use `send_sms()`; settings.SMS_BACKEND picks the transport."""

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import cache

from django.conf import settings
from django.utils.module_loading import import_string

from .phone import mask_phone

logger = logging.getLogger(__name__)


class SmsError(Exception):
    """Raised when the provider refuses or fails to accept a message."""


@dataclass(frozen=True)
class SmsMessage:
    to: str
    body: str


class BaseBackend:
    def send(self, message: SmsMessage) -> None:
        raise NotImplementedError


class ConsoleBackend(BaseBackend):
    """Development only: prints the message (including any code) to the server log."""

    def send(self, message: SmsMessage) -> None:
        logger.warning("SMS to %s: %s", message.to, message.body)


class LocmemBackend(BaseBackend):
    """Tests: keeps messages in `LocmemBackend.outbox`."""

    outbox: list[SmsMessage] = []

    def send(self, message: SmsMessage) -> None:
        LocmemBackend.outbox.append(message)


class AfricasTalkingBackend(BaseBackend):
    LIVE_URL = "https://api.africastalking.com/version1/messaging"
    SANDBOX_URL = "https://api.sandbox.africastalking.com/version1/messaging"
    TIMEOUT_SECONDS = 10

    def send(self, message: SmsMessage) -> None:
        url = self.SANDBOX_URL if settings.AFRICASTALKING_SANDBOX else self.LIVE_URL
        fields = {
            "username": settings.AFRICASTALKING_USERNAME,
            "to": message.to,
            "message": message.body,
        }
        if settings.AFRICASTALKING_SENDER_ID:
            fields["from"] = settings.AFRICASTALKING_SENDER_ID
        request = urllib.request.Request(  # noqa: S310 - fixed https URL above
            url,
            data=urllib.parse.urlencode(fields).encode(),
            headers={"apiKey": settings.AFRICASTALKING_API_KEY, "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.TIMEOUT_SECONDS) as response:  # noqa: S310
                payload = json.loads(response.read())
        except (OSError, ValueError) as exc:
            raise SmsError(f"SMS provider request failed: {exc}") from exc

        recipients = payload.get("SMSMessageData", {}).get("Recipients", [])
        if not recipients or recipients[0].get("status") != "Success":
            status = recipients[0].get("status") if recipients else "no recipients"
            raise SmsError(f"SMS to {mask_phone(message.to)} rejected: {status}")


@cache
def _backend_class(path: str) -> type[BaseBackend]:
    return import_string(path)


def send_sms(to: str, body: str) -> None:
    """Send immediately. Request handlers should use tasks.send_sms_task via on_commit."""
    _backend_class(settings.SMS_BACKEND)().send(SmsMessage(to=to, body=body))
