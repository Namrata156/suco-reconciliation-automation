"""
Read-only case detail shared by the admin screens and the API: the warnings
a maker must see, and ALL dispute rows for the case's RRN (the presence
matrix keeps only the first row per leg; design section 7).
"""
from __future__ import annotations

from datetime import timedelta

from exception_management.models import ATTENTION_RULE_CODES, ExceptionCase
from reconciliation.presence import DEFAULT_OVERLAY_LEGS, adjustment_rows_any_date, overlay_rows_for_date


def annotated(case: ExceptionCase) -> ExceptionCase:
    """The case with the queue annotations (age, bucket, overdue, P5 hint)."""
    if hasattr(case, "is_overdue") and hasattr(case, "previous_day_r12"):
        return case
    return ExceptionCase.objects.with_queue_annotations().select_related("payment_module", "assigned_to").get(pk=case.pk)


def case_flags(case: ExceptionCase) -> dict[str, bool]:
    """Machine-readable flags (API, admin list). `case` must be annotated."""
    return {
        "overdue": bool(getattr(case, "is_overdue", False)),
        "r07x": case.rule_code in ATTENTION_RULE_CODES,
        "needs_review": case.needs_review,
        "multiple_adjustment_rows": case.multiple_adjustment_rows,
        "rrc_already_sent": case.rrc_already_sent_on is not None,
        "no_deadline": case.no_deadline,
        "previous_day_r12": bool(getattr(case, "previous_day_r12", False)),
    }


def case_warnings(case: ExceptionCase) -> list[str]:
    """Human-readable warnings, most important first. `case` must be annotated."""
    out = []
    if case.needs_review:
        out.append(f"Needs review: {case.needs_review_reason}")
    if case.rrc_already_sent_on:
        out.append(case.rrc_warning)
    if case.rule_code in ATTENTION_RULE_CODES:
        out.append("Chargeback more than 35 days after the original transaction: check whether the response "
                   "deadline has already passed (R07x)")
    if getattr(case, "is_overdue", False):
        out.append(f"Overdue (due {case.due_date:%Y-%m-%d}{f' {case.due_cutoff_time:%H:%M}' if case.due_cutoff_time else ''})")
    if case.no_deadline:
        out.append("No deadline: the rule's TAT was not found for this case (A4)")
    if case.multiple_adjustment_rows:
        out.append("Multiple adjustment rows for this RRN on this date: check every row below")
    if getattr(case, "previous_day_r12", False):
        out.append(f"An R12 (NPCI only, successful) result exists for this RRN on "
                   f"{case.business_date - timedelta(days=1):%Y-%m-%d}: possibly the same payment recorded by NPCI the "
                   "previous day (information only, not linked)")
    if case.result_missing:
        out.append("The RRN has no current rule result any more (its match group was superseded)")
    return out


def _row(leg, fact, cells, original_not_loaded=None, on_case_date=True) -> dict:
    return {
        "leg": leg,
        "fact_id": fact.pk,
        "file_date": fact.business_date.isoformat(),
        "amount": None if fact.amount is None else str(fact.amount),
        "direction": fact.direction,
        "response_code": cells.get("response_code", ""),
        "ssk_side": cells.get("ssk_side", ""),
        "dispute_type": cells.get("dispute_type", ""),
        "txn_date": cells.get("txn_date", ""),
        "original_not_loaded": original_not_loaded,
        "belongs_to_case_date": on_case_date,
    }


def overlay_rows(case: ExceptionCase) -> list[dict]:
    """Every DRC / Timeout / Adjustment row belonging to the case's date for
    its RRN, then Adjustment rows for the RRN on other loaded dates."""
    rows, seen = [], set()
    for leg in DEFAULT_OVERLAY_LEGS:
        for fact, cells, onl in overlay_rows_for_date(case.payment_module, case.business_date, leg).get(case.rrn, []):
            rows.append(_row(leg, fact, cells, onl))
            seen.add(fact.pk)
    for fact, cells in adjustment_rows_any_date(case.payment_module).get(case.rrn, []):
        if fact.pk not in seen:
            rows.append(_row("ADJUSTMENT", fact, cells, on_case_date=False))
    return rows
