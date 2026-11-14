"""Payment providers. The provider is not chosen yet (MTN MoMo, Airtel Money or an
aggregator), so two are built in:

- `manual` (pilot default): the customer pays the business mobile-money number quoting the
  booking reference; finance records the provider transaction ID from the merchant statement.
- `dev`: a simulated checkout whose events arrive through the same signed-webhook path a real
  provider will use. Production refuses to start with it.

A real provider subclasses `SignedWebhookProvider` (or overrides `parse_webhook` for the
provider's own signature scheme) and implements `checkout`.
"""

import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

SIGNATURE_HEADER = "HTTP_CRIB_SIGNATURE"


class WebhookError(Exception):
    """The request is not a valid, signed provider event. Never shown to the caller."""


@dataclass(frozen=True)
class Checkout:
    redirect_url: str | None
    instructions: str


@dataclass(frozen=True)
class ProviderEvent:
    event_id: str
    reference: str
    succeeded: bool
    amount_ugx: int
    currency: str
    provider_txn_id: str


class ManualProvider:
    name = "manual"
    has_checkout = False

    def instructions(self, reference: str) -> str:
        return (
            f"Pay by mobile money to {settings.PAYMENT_MERCHANT_NUMBER} with reference {reference}."
        )

    def checkout(self, payment: object) -> Checkout:
        return Checkout(None, self.instructions(payment.appointment.reference))

    def parse_webhook(self, body: bytes, meta: dict) -> ProviderEvent:
        raise WebhookError("Manual payments have no webhook.")


def sign(secret: str, timestamp: int, body: bytes) -> str:
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class SignedWebhookProvider:
    """Header `Crib-Signature: t=<unix>,v1=<hex HMAC-SHA256 of "t.body">`."""

    name = "signed"
    has_checkout = True

    def _secret(self) -> str:
        secret = settings.PAYMENT_WEBHOOK_SECRET
        if not secret:
            raise ImproperlyConfigured("PAYMENT_WEBHOOK_SECRET is not set.")
        return secret

    def instructions(self, reference: str) -> str:
        return "Open your viewing on Crib Link to pay."

    def checkout(self, payment: object) -> Checkout:  # pragma: no cover - provider-specific
        raise NotImplementedError

    def parse_webhook(self, body: bytes, meta: dict) -> ProviderEvent:
        header = meta.get(SIGNATURE_HEADER, "")
        parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
        try:
            timestamp = int(parts["t"])
            received = parts["v1"]
        except (KeyError, ValueError) as exc:
            raise WebhookError("Missing or malformed signature.") from exc
        if abs(time.time() - timestamp) > settings.PAYMENT_WEBHOOK_TOLERANCE_SECONDS:
            raise WebhookError("Stale signature.")
        if not hmac.compare_digest(sign(self._secret(), timestamp, body), received):
            raise WebhookError("Bad signature.")
        try:
            data = json.loads(body)
            return ProviderEvent(
                event_id=str(data["id"]),
                reference=str(data["reference"]),
                succeeded=data["status"] == "succeeded",
                amount_ugx=int(data["amount"]),
                currency=str(data["currency"]),
                provider_txn_id=str(data["transaction_id"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise WebhookError("Malformed event.") from exc


class DevProvider(SignedWebhookProvider):
    name = "dev"

    def checkout(self, payment: object) -> Checkout:
        return Checkout(
            reverse("payments:dev_checkout", args=[payment.reference]),
            "Simulated checkout (development only).",
        )


PROVIDERS = {"manual": ManualProvider, "dev": DevProvider}


def get_provider() -> ManualProvider | SignedWebhookProvider:
    try:
        return PROVIDERS[settings.PAYMENT_PROVIDER]()
    except KeyError as exc:
        raise ImproperlyConfigured(
            f"Unknown PAYMENT_PROVIDER {settings.PAYMENT_PROVIDER!r}"
        ) from exc
