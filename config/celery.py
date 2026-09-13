"""
Celery application instance for the Enterprise Payment Reconciliation
Platform. Imported by config/__init__.py so `@shared_task`-decorated tasks
registered anywhere in the project (e.g. file_processing.tasks) are bound to
this app automatically.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")

# Read CELERY_* settings from Django settings.py (namespace="CELERY" means
# every celery setting must be prefixed CELERY_ there, e.g. CELERY_BROKER_URL).
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py in each INSTALLED_APPS app (e.g. file_processing/tasks.py).
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")