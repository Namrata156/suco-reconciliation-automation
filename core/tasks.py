"""Thin Celery wrapper (for when a worker + beat are deployed; none runs
today -- `manage.py send_notifications` is the scheduled entry point)."""
from __future__ import annotations

from celery import shared_task

from core.notifications import deliver_pending


@shared_task
def deliver_notifications() -> dict:
    r = deliver_pending()
    return {"enabled": r.enabled, "sent": r.sent, "retry_later": r.retry_later, "failed": r.failed}
