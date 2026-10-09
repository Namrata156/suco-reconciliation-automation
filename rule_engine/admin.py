"""Read-only admin for the rule repository. Editing rules is out of scope:
rule changes need maker-checker approval (Phase 3), and versions are
immutable anyway -- a change is a new version, seeded from an approved doc."""
from __future__ import annotations

from django.contrib import admin

from rule_engine.models import AgeingBucket, ResponseCode, Rule, RuleSet, RuleSetStatus, RuleTat


class ReadOnlyAdminMixin:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class RuleInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = Rule
    extra = 0
    fields = ("evaluation_order", "code", "name", "status_category", "priority", "response_code", "tat_type", "tat_days", "tat_day_basis")
    readonly_fields = fields
    show_change_link = True


class RuleTatInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = RuleTat
    extra = 0
    fields = ("evaluation_order", "label", "condition", "tat_days", "tat_day_basis", "source", "notes")
    readonly_fields = fields


@admin.register(RuleSet)
class RuleSetAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("payment_module", "version_label", "status", "effective_from", "activated_at", "retired_at", "created_by", "created_at")
    list_filter = ("payment_module", "status")
    inlines = [RuleInline]


@admin.register(Rule)
class RuleAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("code", "rule_set", "evaluation_order", "name", "status_category", "priority", "response_code", "tat_type", "tat_days", "tat_day_basis")
    list_filter = ("rule_set", "status_category", "priority", "tat_type")
    search_fields = ("code", "name")
    ordering = ("rule_set", "evaluation_order")
    inlines = [RuleTatInline]


@admin.register(ResponseCode)
class ResponseCodeAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("flag", "code_display", "description", "used_by_active", "source", "payment_module")
    list_filter = ("payment_module", "flag")
    search_fields = ("code", "description")

    @admin.display(description="Code")
    def code_display(self, obj):
        return obj.code or "(unknown)"

    @admin.display(description="Used by (active rule set)")
    def used_by_active(self, obj):
        active = RuleSet.objects.filter(payment_module_id=obj.payment_module_id, status=RuleSetStatus.ACTIVE).first()
        return ", ".join(obj.used_by(active)) if active else "-"


@admin.register(AgeingBucket)
class AgeingBucketAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("payment_module", "order", "label", "min_days", "max_days", "boundary_reason", "source")
    list_filter = ("payment_module",)
