"""Read-only admin views of the NTSL mapping, the self-check results, the
settlement results, the tolerance and the settlement-case links. Changes go
through the services (settlement.mapping_changes, settlement.tolerance,
settlement.reconcile / cases), never through the admin. Read access is the
model view permission each role group holds (migration settlement/0004).
Settlement CASES are worked in the Phase 3 case queue
(exception_management admin / API)."""
from __future__ import annotations

import json

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from settlement.models import (
    NtslCategoryMapEntry,
    NtslCategoryMapVersion,
    NtslCycleCheck,
    NtslMappingEvent,
    SettlementCase,
    SettlementCycleResult,
    SettlementResult,
    SettlementTolerance,
    SettlementToleranceEvent,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyInline(admin.TabularInline):
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class EntryInline(ReadOnlyInline):
    model = NtslCategoryMapEntry
    fields = ("match_type", "pattern", "category", "txn_type", "priority", "notes")
    readonly_fields = fields


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


# -- Slice 2 ------------------------------------------------------------------------
class CycleRowInline(ReadOnlyInline):
    model = SettlementCycleResult
    fields = ("settlement_cycle", "side", "ntsl_amount", "ntsl_count", "matched_amount", "matched_count",
              "explained_amount", "explained_count", "unexplained_amount", "unexplained_count", "within_tolerance",
              "detail")
    readonly_fields = fields
    ordering = ("settlement_cycle_id", "side")


@admin.register(SettlementResult)
class SettlementResultAdmin(ReadOnlyAdmin):
    list_display = ("business_date", "payment_module", "status", "unexplained_amount", "unexplained_count",
                    "sop_difference", "sop_unexplained", "fees_net", "tolerance_amount", "is_current", "computed_at",
                    "case_link")
    list_filter = ("status", "is_current", "business_date", "payment_module")
    inlines = [CycleRowInline]
    fieldsets = (
        ("Result", {"fields": ("payment_module", "business_date", "status", "reasons", "case_link", "is_current",
                               "superseded_at", "computed_at", "source", "map_version", "rule_run")}),
        ("Tolerance (Q4)", {"fields": ("tolerance", "tolerance_amount", "tolerance_count")}),
        ("SOP check (C1): (CBS deposits - withdrawals) - (NTSL debit - credit) = explained items",
         {"fields": ("sop_cbs_net", "sop_ntsl_net", "sop_difference", "sop_explained", "sop_unexplained")}),
        ("Totals", {"fields": ("unexplained_amount", "unexplained_count", "fees_net")}),
        ("Calculation snapshot", {"classes": ("collapse",), "fields": ("snapshot_pretty",)}),
    )
    readonly_fields = ("payment_module", "business_date", "status", "reasons", "case_link", "is_current",
                       "superseded_at", "computed_at", "source", "map_version", "rule_run", "tolerance",
                       "tolerance_amount", "tolerance_count", "sop_cbs_net", "sop_ntsl_net", "sop_difference",
                       "sop_explained", "sop_unexplained", "unexplained_amount", "unexplained_count", "fees_net",
                       "snapshot_pretty")

    @admin.display(description="Settlement case")
    def case_link(self, obj):
        link = SettlementCase.objects.filter(payment_module_id=obj.payment_module_id,
                                             business_date=obj.business_date).first()
        if link is None:
            return "-"
        url = reverse("admin:exception_management_exceptioncase_change", args=[link.case_id])
        return format_html('<a href="{}">case {}</a>', url, link.case_id)

    @admin.display(description="Snapshot")
    def snapshot_pretty(self, obj):
        return format_html("<pre style='max-height:40em;overflow:auto'>{}</pre>",
                           json.dumps(obj.snapshot, indent=1, sort_keys=True))


class ToleranceEventInline(ReadOnlyInline):
    model = SettlementToleranceEvent
    fields = ("created_at", "event_type", "actor_username", "remarks", "changes", "self_approved")
    readonly_fields = fields


@admin.register(SettlementTolerance)
class SettlementToleranceAdmin(ReadOnlyAdmin):
    list_display = ("payment_module", "version", "status", "amount", "txn_count", "proposed_by_username",
                    "decided_by_username", "self_approved", "activated_at")
    list_filter = ("payment_module", "status")
    inlines = [ToleranceEventInline]


@admin.register(SettlementToleranceEvent)
class SettlementToleranceEventAdmin(ReadOnlyAdmin):
    list_display = ("tolerance", "event_type", "actor_username", "self_approved", "created_at")
    list_filter = ("event_type",)


@admin.register(SettlementCase)
class SettlementCaseAdmin(ReadOnlyAdmin):
    list_display = ("business_date", "payment_module", "case", "result", "first_result")
    list_filter = ("payment_module",)
