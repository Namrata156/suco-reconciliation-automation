"""
Thin Celery wrapper around rule_engine.evaluator.evaluate_business_date,
same style as reconciliation.tasks.generate_presence_matrix. The evaluator
has no Celery dependency and is tested by direct calls.
"""
from __future__ import annotations

from datetime import date

from celery import shared_task

from core.models import PaymentModule
from rule_engine.evaluator import evaluate_business_date


@shared_task
def evaluate_rules(payment_module_id: int, business_date_iso: str) -> dict:
    payment_module = PaymentModule.objects.get(pk=payment_module_id)
    business_date_ = date.fromisoformat(business_date_iso)
    summary = evaluate_business_date(payment_module, business_date_, triggered_by="celery:evaluate_rules")
    return summary.as_dict()
