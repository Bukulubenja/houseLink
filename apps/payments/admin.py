from django.contrib import admin

from apps.listings.admin import ReadOnlyAdmin

from .models import Payment, Refund, WebhookEvent


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = ("reference", "appointment", "amount_ugx", "status", "provider", "reconciled_at")
    list_filter = ("status", "provider")
    search_fields = ("reference", "provider_txn_id", "appointment__reference")


@admin.register(Refund)
class RefundAdmin(ReadOnlyAdmin):
    list_display = ("payment", "amount_ugx", "status", "requested_by", "paid_by")
    list_filter = ("status",)


@admin.register(WebhookEvent)
class WebhookEventAdmin(ReadOnlyAdmin):
    list_display = ("provider", "event_id", "outcome", "received_at")
