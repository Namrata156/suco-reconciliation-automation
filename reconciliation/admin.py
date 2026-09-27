"""Read-only admin visibility for the Presence Matrix, matching every
other app's admin-first convention (no upload API/frontend exists yet)."""
from __future__ import annotations

from django.contrib import admin

from reconciliation.models import (
    RegisterPresence,
    RegisterPresenceHistory,
    TransactionMatchGroup,
    TransactionMatchGroupHistory,
)


class RegisterPresenceInline(admin.TabularInline):
    model = RegisterPresence
    extra = 0
    fields = ("leg", "present", "transaction_record", "amount", "direction")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(TransactionMatchGroup)
class TransactionMatchGroupAdmin(admin.ModelAdmin):
    list_display = (
        "payment_module",
        "rrn",
        "business_date",
        "settlement_cycle",
        "amount_match",
        "status_match",
        "processing_status",
    )
    list_filter = ("payment_module", "processing_status", "business_date")
    search_fields = ("rrn",)
    date_hierarchy = "business_date"
    inlines = [RegisterPresenceInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RegisterPresence)
class RegisterPresenceAdmin(admin.ModelAdmin):
    list_display = ("match_group", "leg", "present", "transaction_record", "amount", "direction")
    list_filter = ("leg", "present")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TransactionMatchGroupHistory)
class TransactionMatchGroupHistoryAdmin(admin.ModelAdmin):
    """Schema-only history table (archival job is Phase 3) -- read-only."""

    list_display = (
        "original_id",
        "payment_module",
        "rrn",
        "business_date",
        "processing_status",
        "archived_at",
        "archival_reason",
    )
    list_filter = ("payment_module", "processing_status")
    search_fields = ("rrn", "original_id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RegisterPresenceHistory)
class RegisterPresenceHistoryAdmin(admin.ModelAdmin):
    """Schema-only history table (archival job is Phase 3) -- read-only."""

    list_display = (
        "original_id",
        "match_group_id",
        "leg",
        "present",
        "transaction_record_id",
        "amount",
        "direction",
        "archived_at",
    )
    list_filter = ("leg", "present")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
