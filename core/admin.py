from django.contrib import admin

from core.models import BankHoliday, PaymentModule, SettlementCycle, UserActivity


@admin.register(PaymentModule)
class PaymentModuleAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    search_fields = ("code", "name")


@admin.register(SettlementCycle)
class SettlementCycleAdmin(admin.ModelAdmin):
    list_display = (
        "payment_module", "cycle_code", "cycle_type", "start_time", "end_time", "rtgs_settlement_time", "is_active"
    )
    list_filter = ("payment_module", "cycle_type", "is_active")


@admin.register(BankHoliday)
class BankHolidayAdmin(admin.ModelAdmin):
    """Read-only: the list is seeded from RBI's published matrix (migration
    core/0005, `seed_bank_holidays`). Ops' yearly confirmation / additions
    land as a separate, approved data migration, not an admin edit (the
    2026 Karnataka confirmation is core/0006)."""

    list_display = ("date", "weekday", "name", "region", "confirmed_by_ops", "retrieved_on", "source_url")
    list_filter = ("region", "confirmed_by_ops")
    search_fields = ("name",)
    date_hierarchy = "date"

    @admin.display(description="Day")
    def weekday(self, obj):
        return obj.date.strftime("%a")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserActivity)
class UserActivityAdmin(admin.ModelAdmin):
    """Read-only user activity log (login / logout / failed login). Visible to
    roles holding core.view_useractivity (Administrator, Auditor)."""

    list_display = ("created_at", "activity_type", "username", "user", "ip_address", "path")
    list_filter = ("activity_type",)
    search_fields = ("username", "ip_address")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "activity_type", "user", "username", "ip_address", "user_agent", "path")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
