from django.apps import AppConfig


class SettlementConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "settlement"
    verbose_name = "Settlement Control / NTSL Validation"

    def ready(self):
        # Delete guards for the append-only tables (cascades never call delete()).
        from core.append_only import guard_deletes
        from settlement.models import NtslCategoryMapEntry, NtslMappingEvent

        guard_deletes(NtslCategoryMapEntry)
        guard_deletes(NtslMappingEvent)
