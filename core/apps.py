from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core (shared reference data)"

    def ready(self):
        # User activity log (Phase 3 design section 5): auth signal receivers
        # and the append-only delete guard.
        from core import signals  # noqa: F401
        from core.append_only import guard_deletes
        from core.models import Notification, UserActivity

        guard_deletes(UserActivity)
        guard_deletes(Notification)
