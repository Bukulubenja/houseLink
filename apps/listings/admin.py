from django.contrib import admin

from .models import Property, PropertyContact, Verification


class ReadOnlyAdmin(admin.ModelAdmin):
    """Overview only. Every change goes through /ops/ so it is validated and audited."""

    exclude: tuple[str, ...] = ()

    def get_readonly_fields(self, request, obj=None) -> list[str]:
        return [f.name for f in self.model._meta.fields if f.name not in self.exclude]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Property)
class PropertyAdmin(ReadOnlyAdmin):
    list_display = ("title", "area", "rent_ugx", "status", "last_verified_at", "recheck_due_at")
    list_filter = ("status", "area", "kind")
    search_fields = ("title", "location_hint")
    exclude = ("authority_document",)


@admin.register(PropertyContact)
class PropertyContactAdmin(ReadOnlyAdmin):
    list_display = ("name", "relationship", "phone", "created_at")
    search_fields = ("name", "phone")


@admin.register(Verification)
class VerificationAdmin(ReadOnlyAdmin):
    list_display = ("property", "verified_by", "checked_at", "decision", "decided_by")
    list_filter = ("decision",)
