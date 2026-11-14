from django.contrib import admin

from .models import IdentitySubmission, LandlordProfile


@admin.register(LandlordProfile)
class LandlordProfileAdmin(admin.ModelAdmin):
    list_display = ("legal_name", "user", "identity_status", "nin_last4", "id_verified_at")
    list_filter = ("identity_status",)
    search_fields = ("legal_name", "user__phone", "nin_last4")
    readonly_fields = [f.name for f in LandlordProfile._meta.fields]

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(IdentitySubmission)
class IdentitySubmissionAdmin(admin.ModelAdmin):
    """Read-only overview. Decisions and document viewing go through /review/ so they are
    validated and audited; the file fields are hidden here for the same reason."""

    list_display = ("legal_name", "status", "reason", "created_at", "reviewed_by")
    list_filter = ("status", "reason")
    search_fields = ("legal_name", "nin_last4", "profile__user__phone")
    exclude = ("id_front", "id_back", "selfie", "nin_hash")

    def get_readonly_fields(self, request, obj=None) -> list[str]:
        return [f.name for f in self.model._meta.fields if f.name not in self.exclude]

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
