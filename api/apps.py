from django.apps import AppConfig


class ApiConfig(AppConfig):
    """Phase 5 API v1 (/api/v1/...). No models: every view is a thin wrapper
    over an existing service (design docs/phase5_design_draft_v1.txt 3.3)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "api"
    verbose_name = "API v1"
