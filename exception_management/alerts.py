"""
Exception-case alerts (owner-approved plan, 2026-10-11). One call here per
event; each records rows in the core.Notification outbox via
core.notifications.notify (written on commit, mailed later). No lifecycle or
sync logic lives here.

Recipients are the ACTIVE members of the role groups (superusers are not
added unless they are also in the group), or the case's own people:

  R07x case created, or an existing
  case changed to R07x (sync)     Makers + Checkers   one alert per sync run listing those cases; a case
                                                      already R07x before the run is not alerted again
  flagged needs_review (sync)     Checkers            one alert per sync run listing the flagged cases
  submit                          Checkers, except the submitter; if the
                                  submitter is the only Checker, them
                                  (self-approval, owner D3)
  bulk submit                     same as submit      ONE summary alert for the whole bulk request
  reject                          the maker: the case's assignee and the
                                  submitter of the rejected action (one each)
  daily digest                    Makers: open / overdue HIGH, overdue, due today, theirs
                                  Checkers: awaiting approval, needs review
"""
from __future__ import annotations

from collections import Counter

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from core.models import AlertType
from core.notifications import link, notify
from exception_management.models import (
    ACTIVE_STATUSES,
    CaseStatus,
    Decision,
    ExceptionCase,
    ProposedAction,
)
from exception_management.roles import Role

_LIST_LIMIT = 50


def _iso(d) -> str:
    """Dates in alerts are ISO (YYYY-MM-DD), as ops' files and the queue use them."""
    return d.isoformat() if d is not None else ""


def role_members(*roles):
    return list(get_user_model().objects.filter(is_active=True, groups__name__in=roles).distinct().order_by("pk"))


def case_link(case) -> str:
    return link(reverse("admin:exception_management_exceptioncase_change", args=[case.pk]))


def queue_link(query: str = "") -> str:
    return link(reverse("admin:exception_management_exceptioncase_changelist") + query)


def approval_recipients(submitter) -> list:
    checkers = role_members(Role.CHECKER)
    others = [u for u in checkers if u.pk != submitter.pk]
    return others or [u for u in checkers if u.pk == submitter.pk]


# -- events ---------------------------------------------------------------------
def r07x_cases_created(business_date, cases) -> None:
    if not cases:
        return
    notify(AlertType.R07X_CASE, role_members(Role.MAKER, Role.CHECKER), {
        "business_date": _iso(business_date),
        "cases": [{"rrn": c.rrn, "priority": c.priority, "due_date": _iso(c.due_date), "link": case_link(c)}
                  for c in cases],
    }, related=cases[0] if len(cases) == 1 else None)


def cases_flagged_for_review(business_date, cases) -> None:
    if not cases:
        return
    notify(AlertType.NEEDS_REVIEW, role_members(Role.CHECKER), {
        "business_date": _iso(business_date),
        "cases": [{"rrn": c.rrn, "status": c.status, "reason": c.needs_review_reason.splitlines()[-1],
                   "link": case_link(c)} for c in cases[:_LIST_LIMIT]],
    }, related=cases[0] if len(cases) == 1 else None)


def action_submitted(case, action) -> None:
    notify(AlertType.SUBMITTED, approval_recipients(action.submitted_by), {
        "rrn": case.rrn, "business_date": _iso(case.business_date), "rule_code": case.rule_code, "priority": case.priority,
        "action_type": action.action_type, "action_label": action.get_action_type_display(),
        "response_code": str(action.response_code) if action.response_code_id else "", "remarks": action.remarks,
        "submitted_by": action.submitted_by.get_username(), "link": case_link(case),
    }, related=case)


def bulk_submitted(user, cases, actions) -> None:
    if not actions:
        return
    a = actions[0]
    notify(AlertType.BULK_SUBMITTED, approval_recipients(user), {
        "count": len(actions), "action_label": a.get_action_type_display(),
        "response_code": str(a.response_code) if a.response_code_id else "", "remarks": a.remarks,
        "submitted_by": user.get_username(),
        "business_dates": ", ".join(sorted({c.business_date.isoformat() for c in cases})),
        "cases": [{"rrn": c.rrn, "business_date": _iso(c.business_date), "rule_code": c.rule_code}
                  for c in cases[:_LIST_LIMIT]],
        "more": max(0, len(cases) - _LIST_LIMIT),
        "link": queue_link("?mine=approval"),
    })


def action_rejected(case, action, checker) -> None:
    makers = [u for u in (case.assigned_to, action.submitted_by) if u is not None]
    notify(AlertType.REJECTED, makers, {
        "rrn": case.rrn, "business_date": _iso(case.business_date), "rule_code": case.rule_code,
        "action_type": action.action_type, "action_label": action.get_action_type_display(),
        "reason": action.decision_remarks, "rejected_by": checker.get_username(), "link": case_link(case),
    }, related=case)


# -- daily digest -------------------------------------------------------------------
_MAKER_WORK = (CaseStatus.OPEN, CaseStatus.IN_PROGRESS)
_MAKER_ALL = (CaseStatus.OPEN, CaseStatus.IN_PROGRESS, CaseStatus.APPROVED)


def digest_counts(now=None) -> dict:
    """Queue-wide counts for the digest (overdue as in the queue: due date +
    16:00 RET cutoff, RuleResultQuerySet.with_ageing)."""
    now = timezone.localtime(now) if now else timezone.localtime()
    today = now.date()
    rows = list(ExceptionCase.objects.filter(status__in=ACTIVE_STATUSES).with_ageing(now=now)
                .values("status", "priority", "is_overdue", "due_date", "assigned_to_id", "needs_review"))
    c = Counter()
    mine, mine_overdue = Counter(), Counter()
    for r in rows:
        s, overdue = r["status"], bool(r["is_overdue"])
        if s in _MAKER_WORK and r["priority"] == "HIGH":
            c["high_open"] += 1
            c["high_overdue"] += overdue
        if s in _MAKER_ALL:
            c["overdue"] += overdue
            c["due_today"] += r["due_date"] == today
        if s == CaseStatus.APPROVED:
            c["approved_waiting"] += 1
        if s == CaseStatus.PENDING_APPROVAL:
            c["awaiting"] += 1
            c["awaiting_high"] += r["priority"] == "HIGH"
        if r["assigned_to_id"] and s in (CaseStatus.IN_PROGRESS, CaseStatus.APPROVED):
            mine[r["assigned_to_id"]] += 1
            mine_overdue[r["assigned_to_id"]] += overdue
    c["needs_review"] = ExceptionCase.objects.filter(needs_review=True).count()
    submitted = Counter(ProposedAction.objects.filter(decision=Decision.PENDING).values_list("submitted_by_id", flat=True))
    return {"today": today, "totals": c, "mine": mine, "mine_overdue": mine_overdue, "submitted": submitted}


def send_digest(now=None, *, dry_run: bool = False) -> dict:
    """One digest per Maker and per Checker (a user with both roles gets
    both), at most once a day each (dedupe key). Returns what was / would be
    sent per role."""
    d = digest_counts(now)
    t, today = d["totals"], d["today"]
    makers, checkers = role_members(Role.MAKER), role_members(Role.CHECKER)
    maker_fields = ("high_open", "high_overdue", "overdue", "due_today", "approved_waiting")
    checker_fields = ("awaiting", "awaiting_high", "needs_review")
    summary = {
        "date": today.isoformat(),
        Role.MAKER: {"recipients": len(makers), **{k: t[k] for k in maker_fields}},
        Role.CHECKER: {"recipients": len(checkers), **{k: t[k] for k in checker_fields}},
    }
    if dry_run:
        return summary
    for u in makers:
        notify(AlertType.DIGEST_MAKER, [u], {
            "today": _iso(today), "name": u.get_full_name() or u.get_username(), **{k: t[k] for k in maker_fields},
            "mine": d["mine"][u.pk], "mine_overdue": d["mine_overdue"][u.pk], "link": queue_link("?mine=assigned"),
        }, dedupe_key=f"digest:maker:{today}")
    for u in checkers:
        notify(AlertType.DIGEST_CHECKER, [u], {
            "today": _iso(today), "name": u.get_full_name() or u.get_username(), **{k: t[k] for k in checker_fields},
            "awaiting_mine": d["submitted"][u.pk], "link": queue_link("?mine=approval"),
        }, dedupe_key=f"digest:checker:{today}")
    return summary
