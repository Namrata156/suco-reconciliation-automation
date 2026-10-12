"""Thin Celery wrapper around settlement.reconcile.reconcile_settlement_date,
same style as rule_engine.tasks.evaluate_rules. Not chained after
evaluate_rules automatically: nothing in the pipeline chains tasks today
(each is queued on its own), and settlement also needs the date's NTSL
files. Queue it after evaluate_rules for the date."""
from __future__ import annotations

from datetime import date

from celery import shared_task

from core.models import PaymentModule
from settlement.reconcile import reconcile_settlement_date


@shared_task
def reconcile_settlement(payment_module_id: int, business_date_iso: str) -> dict:
    payment_module = PaymentModule.objects.get(pk=payment_module_id)
    outcome = reconcile_settlement_date(payment_module, date.fromisoformat(business_date_iso),
                                        source="celery:reconcile_settlement")
    r = outcome.result
    return {"result_id": r.pk, "status": r.status, "reasons": r.reasons, "case": outcome.case_action,
            "case_id": outcome.case_id}
