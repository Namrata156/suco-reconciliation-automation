"""Thin Celery wrapper (for when a worker + beat are deployed; none runs
today -- `manage.py send_alert_digest` is the scheduled entry point)."""
from __future__ import annotations

from celery import shared_task

from exception_management.alerts import send_digest


@shared_task
def send_alert_digest() -> dict:
    return send_digest()
