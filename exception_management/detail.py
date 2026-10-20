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
        "settlement": case.is_settlement,
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
    if case.is_settlement:
        out.append("Settlement case: NTSL and the system do not tie for this business date. Record the explanation "
                   "(no money moves in the system); the checker accepts it.")
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


def settlement_detail(case: ExceptionCase) -> dict | None:
    """A settlement case's result (Phase 4 S5): status, reasons, SOP check
    and the cycle x side rows. None for a transaction case. The full
    calculation snapshot stays on the result (settlement admin)."""
    if not case.is_settlement:
        return None
    link = getattr(case, "settlement_case", None)
    if link is None:
        return None
    body = settlement_result_payload(link.result)
    return {"result_id": body.pop("result_id"), "first_result_id": link.first_result_id, **body}


def settlement_result_payload(r) -> dict:
    """One settlement.SettlementResult: status, reasons, tolerance, SOP check,
    fees and the cycle x side rows. Shared by the settlement case detail and
    the per-date settlement screen (Phase 5 design 4.14), which also shows
    TALLIED dates (no case)."""
    m = lambda v: None if v is None else f"{v:.2f}"  # noqa: E731
    return {
        "result_id": r.pk,
        "result_is_current": r.is_current,
        "status": r.status,
        "computed_at": r.computed_at,
        "reasons": r.reasons,
        "tolerance": {"amount": m(r.tolerance_amount), "txn_count": r.tolerance_count},
        "sop": {"cbs_net": m(r.sop_cbs_net), "ntsl_net": m(r.sop_ntsl_net), "difference": m(r.sop_difference),
                "explained": m(r.sop_explained), "unexplained": m(r.sop_unexplained)},
        "fees_net": m(r.fees_net),
        "cycles": [
            {"cycle": c.settlement_cycle.cycle_code if c.settlement_cycle_id else None, "side": c.side,
             "ntsl_amount": m(c.ntsl_amount), "ntsl_count": c.ntsl_count, "matched_amount": m(c.matched_amount),
             "matched_count": c.matched_count, "explained_amount": m(c.explained_amount),
             "explained_count": c.explained_count, "unexplained_amount": m(c.unexplained_amount),
             "unexplained_count": c.unexplained_count, "within_tolerance": c.within_tolerance}
            for c in r.cycle_rows.select_related("settlement_cycle").order_by("settlement_cycle_id", "side")
        ],
    }


def date_overlay_rows(payment_module, business_date, rrn: str) -> list[dict]:
    """Every DRC / Timeout / Adjustment row belonging to one business date's
    group for the RRN (reconciliation.presence.overlay_rows_for_date)."""
    rows = []
    for leg in DEFAULT_OVERLAY_LEGS:
        for fact, cells, onl in overlay_rows_for_date(payment_module, business_date, leg).get(rrn, []):
            rows.append(_row(leg, fact, cells, onl))
    return rows


def overlay_rows(case: ExceptionCase) -> list[dict]:
    """Every DRC / Timeout / Adjustment row belonging to the case's date for
    its RRN, then Adjustment rows for the RRN on other loaded dates. None for
    a settlement case (it has no RRN)."""
    if case.is_settlement:
        return []
    rows = date_overlay_rows(case.payment_module, case.business_date, case.rrn)
    seen = {r["fact_id"] for r in rows}
    for fact, cells in adjustment_rows_any_date(case.payment_module).get(case.rrn, []):
        if fact.pk not in seen:
            rows.append(_row("ADJUSTMENT", fact, cells, on_case_date=False))
    return rows
