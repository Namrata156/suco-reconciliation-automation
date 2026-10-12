"""
Settlement cases (Phase 4 design S5, Q2; Slice 2). One case per payment
module + business date, raised in the Phase 3 exception queue when the
settlement result is UNEXPLAINED (none when TALLIED). It is an
exception_management.ExceptionCase of case type SETTLEMENT (no RRN, match
group or rule result) plus a settlement.SettlementCase row pointing it at the
result -- so the snapshot and the cycle detail -- it is about. It then goes
through the Phase 3 lifecycle (exception_management.services): a Maker
records the explanation (action SETTLEMENT_EXPLANATION), a Checker accepts
it, every step in the append-only CaseEvent log. Never handled in bulk.

Called by settlement.reconcile after every run, in the same transaction:

  result UNEXPLAINED, no case          -> case created (OPEN, HIGH) + CREATED
                                          event + alert to Makers + Checkers
  result NOT_READY                     -> nothing changes (nothing was compared)
  same figures as the linked result    -> re-linked to the new result silently
  figures changed, case not decided    -> RESULT_UPDATED event; a pending
    (OPEN / IN_PROGRESS / PENDING_        explanation is WITHDRAWN and the case
    APPROVAL)                             goes back to IN_PROGRESS (the maker
                                          explained other figures)
  figures changed, APPROVED/COMPLETED  -> flagged needs_review (FLAGGED_FOR_
                                          REVIEW event + alert to Checkers)
  result TALLIED, case exists          -> never deleted or closed: flagged
                                          needs_review (whatever its status),
                                          a Checker clears it with a remark
                                          (services.clear_review)
"""
from __future__ import annotations

from django.urls import reverse
from django.utils import timezone

from core.models import AlertType
from core.notifications import link, notify
from exception_management.actions import ActionType
from exception_management.alerts import role_members
from exception_management.models import (
    DECIDED_STATUSES,
    CaseStatus,
    CaseType,
    Decision,
    EventType,
    ExceptionCase,
    ProposedAction,
)
from exception_management.roles import Role
from exception_management.services import record_event
from rule_engine.models import Priority, StatusCategory
from settlement.models import ResultStatus, SettlementCase, SettlementResult

# A settlement case's queue fields. HIGH: a settlement difference is money
# not accounted for (and HIGH keeps it out of every bulk path as well).
CASE_PRIORITY = Priority.HIGH
CASE_STATUS_CATEGORY = StatusCategory.EXCEPTION
_LIST_LIMIT = 24


def figures(result: SettlementResult) -> dict:
    """What a settlement case is about: the status and every non-zero
    unexplained figure (JSON-safe, comparable between runs)."""
    rows = [
        [r.settlement_cycle.cycle_code if r.settlement_cycle_id else None, r.side,
         f"{r.unexplained_amount:.2f}", r.unexplained_count]
        for r in result.cycle_rows.select_related("settlement_cycle").order_by("settlement_cycle_id", "side")
        if r.unexplained_amount or r.unexplained_count
    ]
    return {
        "status": result.status,
        "unexplained": rows,
        "sop_unexplained": None if result.sop_unexplained is None else f"{result.sop_unexplained:.2f}",
        "reasons": result.reasons,
    }


def _case_link(case) -> str:
    return link(reverse("admin:exception_management_exceptioncase_change", args=[case.pk]))


def _alert_created(case, result: SettlementResult) -> None:
    f = figures(result)
    notify(AlertType.SETTLEMENT_CASE, role_members(Role.MAKER, Role.CHECKER), {
        "module": case.payment_module.code, "business_date": case.business_date.isoformat(),
        "tolerance_amount": f"{result.tolerance_amount:.2f}", "tolerance_count": result.tolerance_count,
        "rows": [{"cycle": c or "no cycle", "side": s, "amount": a, "count": n} for c, s, a, n in f["unexplained"]][
            :_LIST_LIMIT],
        "sop_unexplained": f["sop_unexplained"] if result.sop_unexplained else "",
        "reasons": [r for r in result.reasons if "unexplained" not in r][:_LIST_LIMIT],
        "link": _case_link(case),
    }, related=case)


def _alert_flagged(case) -> None:
    notify(AlertType.SETTLEMENT_REVIEW, role_members(Role.CHECKER), {
        "module": case.payment_module.code, "business_date": case.business_date.isoformat(), "status": case.status,
        "reason": case.needs_review_reason.splitlines()[-1], "link": _case_link(case),
    }, related=case)


def _flag(case: ExceptionCase, reason: str) -> None:
    case.needs_review = True
    case.needs_review_reason = f"{case.needs_review_reason}\n{reason}".strip() if case.needs_review_reason else reason


def sync_settlement_case(result: SettlementResult, *, source: str = "") -> tuple[str, int | None]:
    """Returns (what happened, case id or None). Must run inside the
    reconcile transaction (settlement.reconcile does)."""
    module, d = result.payment_module, result.business_date
    link_row = (SettlementCase.objects.select_for_update().select_related("result")
                .filter(payment_module=module, business_date=d).first())

    if result.status == ResultStatus.NOT_READY:
        return "not_ready", link_row.case_id if link_row else None

    if link_row is None:
        if result.status != ResultStatus.UNEXPLAINED:
            return "none", None
        case = ExceptionCase.objects.create(
            payment_module=module, business_date=d, case_type=CaseType.SETTLEMENT, rrn="", match_group=None,
            rule_result=None, rule_code="", status_category=CASE_STATUS_CATEGORY, priority=CASE_PRIORITY,
            default_action_type=ActionType.SETTLEMENT_EXPLANATION, status=CaseStatus.OPEN,
        )
        SettlementCase.objects.create(case=case, payment_module=module, business_date=d, result=result,
                                      first_result=result)
        record_event(case, EventType.CREATED, new_status=case.status, source=source,
                     changes={"settlement_result_id": [None, result.pk], "figures": [None, figures(result)]})
        _alert_created(case, result)
        return "created", case.pk

    case = ExceptionCase.objects.select_for_update(of=("self",)).select_related("payment_module").get(
        pk=link_row.case_id)
    old_result = link_row.result
    old, new = figures(old_result), figures(result)
    link_row.result = result
    link_row.save(update_fields=["result"])
    if old == new:
        return "unchanged", case.pk

    now = timezone.now()
    changes = {"settlement_result_id": [old_result.pk, result.pk], "figures": [old, new]}
    old_status = case.status
    if result.status == ResultStatus.TALLIED or old_status in DECIDED_STATUSES:
        why = (f"Settlement re-run {timezone.localtime(now):%Y-%m-%d %H:%M}: now {result.status} (result "
               f"{result.pk}), was {old_result.status} (result {old_result.pk}), while the case was {old_status}")
        _flag(case, why)
        case.save()
        record_event(case, EventType.FLAGGED_FOR_REVIEW, old_status=old_status, new_status=case.status,
                     changes=changes, remarks=why, source=source)
        _alert_flagged(case)
        return "flagged", case.pk

    action = None
    if old_status == CaseStatus.PENDING_APPROVAL:
        action = ProposedAction.objects.select_for_update(of=("self",)).filter(
            case=case, decision=Decision.PENDING).first()
        if action is not None:
            action.decision = Decision.WITHDRAWN
            action.decided_at = now
            action.decision_remarks = "Withdrawn: the settlement figures changed on a re-run"
            action.save()
        case.status = CaseStatus.IN_PROGRESS
    case.save()
    record_event(case, EventType.RESULT_UPDATED, old_status=old_status, new_status=case.status, changes=changes,
                 action=action, source=source)
    return "updated", case.pk
