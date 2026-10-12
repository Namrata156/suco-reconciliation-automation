from django.apps import AppConfig


class SettlementConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "settlement"
    verbose_name = "Settlement Control / NTSL Validation"

    def ready(self):
        # Delete guards for the append-only tables (cascades never call delete()).
        from core.append_only import guard_deletes
        from settlement.models import (
            NtslCategoryMapEntry,
            NtslMappingEvent,
            SettlementCycleResult,
            SettlementToleranceEvent,
        )

        for model in (NtslCategoryMapEntry, NtslMappingEvent, SettlementCycleResult, SettlementToleranceEvent):
            guard_deletes(model)
