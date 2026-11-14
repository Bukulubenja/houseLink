from django.contrib import admin

from .models import BlockedIdentity


@admin.register(BlockedIdentity)
class BlockedIdentityAdmin(admin.ModelAdmin):
    list_display = ("__str__", "kind", "blocked_by", "created_at")
    list_filter = ("kind",)
    readonly_fields = ("kind", "value", "reason", "blocked_by", "created_at", "updated_at")

    def has_add_permission(self, request) -> bool:
        return False  # blocks are created by review decisions so they are always audited

    def has_delete_permission(self, request, obj=None) -> bool:
        return False  # bans are permanent per the launch plan
