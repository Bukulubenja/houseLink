from django.contrib import admin

from apps.listings.admin import ReadOnlyAdmin

from .models import BrokerProfile


@admin.register(BrokerProfile)
class BrokerProfileAdmin(ReadOnlyAdmin):
    list_display = ("user", "status", "id_checked_at", "terms_signed_at")
    list_filter = ("status",)
    search_fields = ("user__phone", "user__full_name")
