from django.apps import AppConfig


class RuleEngineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "rule_engine"
    verbose_name = "Business Rule Engine"

    def ready(self):
        # Registers the m2m_changed guard on Rule.alternative_codes.
        from rule_engine import signals  # noqa: F401
