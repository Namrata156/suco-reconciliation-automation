from django.contrib import admin

from core.models import PaymentModule, SettlementCycle


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
