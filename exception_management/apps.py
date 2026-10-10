from django.apps import AppConfig


class ExceptionManagementConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "exception_management"
    verbose_name = "Exception Management and Maker-Checker"

    def ready(self):
        # Registers the CaseEvent pre_delete guard.
        from exception_management import signals  # noqa: F401
