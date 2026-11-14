from django.contrib import admin

from apps.listings.admin import ReadOnlyAdmin

from .models import Appointment, Feedback, ViewingReport


@admin.register(Appointment)
class AppointmentAdmin(ReadOnlyAdmin):
    list_display = ("reference", "property", "customer", "status", "scheduled_start", "broker")
    list_filter = ("status",)
    search_fields = ("reference", "customer__phone")


@admin.register(ViewingReport)
class ViewingReportAdmin(ReadOnlyAdmin):
    list_display = ("appointment", "broker", "customer_attended", "property_as_listed")


@admin.register(Feedback)
class FeedbackAdmin(ReadOnlyAdmin):
    list_display = ("appointment", "rating", "may_publish", "created_at")
