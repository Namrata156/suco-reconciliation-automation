"""
Thin Celery wrapper around `reconciliation.presence.build_presence_matrix`,
for parity with the existing Celery+Redis pipeline convention
(`file_processing.tasks`). Secondary to the core engine + tests -- the
engine itself (`presence.py`) has no Celery dependency and can be called
directly (see tests), matching `file_processing`'s direct-call testing
pattern.
"""
from __future__ import annotations

from datetime import date

from celery import shared_task

from core.models import PaymentModule
from reconciliation.presence import build_presence_matrix


@shared_task
def generate_presence_matrix(payment_module_id: int, business_date_iso: str) -> dict:
    payment_module = PaymentModule.objects.get(pk=payment_module_id)
    business_date_ = date.fromisoformat(business_date_iso)
    result = build_presence_matrix(payment_module, business_date_)
    return result.as_dict()


@shared_task
def run_business_date(run_id: int) -> dict:
    """The "Run reconciliation" chain for one date (Phase 5 design 4.11):
    presence matrix -> rule evaluation + case sync -> settlement. See
    reconciliation.runs.execute_run."""
    from reconciliation.runs import execute_run

    run = execute_run(run_id)
    return {"run_id": run.pk, "status": run.status, "error": run.error}
