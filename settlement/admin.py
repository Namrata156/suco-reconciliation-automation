"""Read-only admin views of the NTSL mapping and self-check results. Changes
to the mapping go through settlement.mapping_changes (maker-checker), not
through the admin."""
from __future__ import annotations

from django.contrib import admin

from settlement.models import NtslCategoryMapEntry, NtslCategoryMapVersion, NtslCycleCheck, NtslMappingEvent


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class EntryInline(admin.TabularInline):
    model = NtslCategoryMapEntry
    fields = ("match_type", "pattern", "category", "txn_type", "priority", "notes")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(NtslCategoryMapVersion)
class NtslCategoryMapVersionAdmin(ReadOnlyAdmin):
    list_display = ("payment_module", "version", "status", "proposed_by_username", "decided_by_username",
                    "self_approved", "activated_at")
    list_filter = ("payment_module", "status")
    inlines = [EntryInline]


@admin.register(NtslMappingEvent)
class NtslMappingEventAdmin(ReadOnlyAdmin):
    list_display = ("version", "event_type", "actor_username", "self_approved", "created_at")
    list_filter = ("event_type",)


@admin.register(NtslCycleCheck)
class NtslCycleCheckAdmin(ReadOnlyAdmin):
    list_display = ("business_date", "settlement_cycle", "status", "line_count", "unmapped_count",
                    "ntsl_final_net", "map_version", "is_current", "checked_at")
    list_filter = ("status", "is_current", "business_date", "payment_module")
