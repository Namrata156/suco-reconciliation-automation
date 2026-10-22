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

Links (Phase 5 Slice 4, design 4.18): every alert links to the SPA, not the
Django admin. The mail body carries ALERT_LINK_BASE_URL + the route, and the
outbox row keeps the route itself (Notification.link_path) for the in-app
list: /<module>/cases/<id> for one case, else the case queue with its
filters (/<module>/cases?mine=approval ...). The digest counts every module;
its link goes to the first active module's queue.
"""
from __future__ import annotations

from collections import Counter

from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import AlertType, PaymentModule
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


def _rule(case) -> str:
    """The case's rule, or "settlement" for a settlement case (no rule result)."""
    return "settlement" if case.is_settlement else case.rule_code


def role_members(*roles):
    return list(get_user_model().objects.filter(is_active=True, groups__name__in=roles).distinct().order_by("pk"))


def _slug(module_id) -> str:
    code = PaymentModule.objects.filter(pk=module_id).values_list("code", flat=True).first()
    return (code or "").lower()


def case_path(case, slug: str | None = None) -> str:
    """The SPA's case screen: /<module>/cases/<id>."""
    return f"/{slug or _slug(case.payment_module_id)}/cases/{case.pk}"


def queue_path(module_id, **query) -> str:
    """The SPA's case queue with filters (the queue reads them from the address)."""
    qs = urlencode({k: v for k, v in query.items() if v not in (None, "")})
    return f"/{_slug(module_id)}/cases" + (f"?{qs}" if qs else "")


def default_module_id():
    """For alerts that cover every module (the digest): the first active one."""
    return PaymentModule.objects.filter(is_active=True).order_by("code").values_list("pk", flat=True).first()


def case_link(case, slug: str | None = None) -> str:
    return link(case_path(case, slug))


def _cases_path(business_date, cases, flag: str) -> str:
    """One case: its screen. Several: the queue filtered to them."""
    if len(cases) == 1:
        return case_path(cases[0])
    return queue_path(cases[0].payment_module_id, flag=flag, status="all", business_date=_iso(business_date))


def approval_recipients(submitter) -> list:
    checkers = role_members(Role.CHECKER)
    others = [u for u in checkers if u.pk != submitter.pk]
    return others or [u for u in checkers if u.pk == submitter.pk]


# -- events ---------------------------------------------------------------------
def r07x_cases_created(business_date, cases) -> None:
    if not cases:
        return
    slug = _slug(cases[0].payment_module_id)
    notify(AlertType.R07X_CASE, role_members(Role.MAKER, Role.CHECKER), {
        "business_date": _iso(business_date),
        "cases": [{"rrn": c.rrn, "priority": c.priority, "due_date": _iso(c.due_date), "link": case_link(c, slug)}
                  for c in cases],
    }, related=cases[0] if len(cases) == 1 else None, link_path=_cases_path(business_date, cases, "r07x"))


def cases_flagged_for_review(business_date, cases) -> None:
    if not cases:
        return
    slug = _slug(cases[0].payment_module_id)
    notify(AlertType.NEEDS_REVIEW, role_members(Role.CHECKER), {
        "business_date": _iso(business_date),
        "cases": [{"rrn": c.rrn, "status": c.status, "reason": c.needs_review_reason.splitlines()[-1],
                   "link": case_link(c, slug)} for c in cases[:_LIST_LIMIT]],
    }, related=cases[0] if len(cases) == 1 else None,
        link_path=_cases_path(business_date, cases, "needs_review"))


def action_submitted(case, action) -> None:
    path = case_path(case)
    notify(AlertType.SUBMITTED, approval_recipients(action.submitted_by), {
        "rrn": case.label, "business_date": _iso(case.business_date), "rule_code": _rule(case), "priority": case.priority,
        "action_type": action.action_type, "action_label": action.get_action_type_display(),
        "response_code": str(action.response_code) if action.response_code_id else "", "remarks": action.remarks,
        "submitted_by": action.submitted_by.get_username(), "link": link(path),
    }, related=case, link_path=path)


def bulk_submitted(user, cases, actions) -> None:
    if not actions:
        return
    a = actions[0]
    path = queue_path(cases[0].payment_module_id, mine="approval")
    notify(AlertType.BULK_SUBMITTED, approval_recipients(user), {
        "count": len(actions), "action_label": a.get_action_type_display(),
        "response_code": str(a.response_code) if a.response_code_id else "", "remarks": a.remarks,
        "submitted_by": user.get_username(),
        "business_dates": ", ".join(sorted({c.business_date.isoformat() for c in cases})),
        "cases": [{"rrn": c.rrn, "business_date": _iso(c.business_date), "rule_code": c.rule_code}
                  for c in cases[:_LIST_LIMIT]],
        "more": max(0, len(cases) - _LIST_LIMIT),
        "link": link(path),
    }, link_path=path)


def action_rejected(case, action, checker) -> None:
    makers = [u for u in (case.assigned_to, action.submitted_by) if u is not None]
    path = case_path(case)
    notify(AlertType.REJECTED, makers, {
        "rrn": case.label, "business_date": _iso(case.business_date), "rule_code": _rule(case),
        "action_type": action.action_type, "action_label": action.get_action_type_display(),
        "reason": action.decision_remarks, "rejected_by": checker.get_username(), "link": link(path),
    }, related=case, link_path=path)


# -- daily digest -------------------------------------------------------------------
_MAKER_WORK = (CaseStatus.OPEN, CaseStatus.IN_PROGRESS)
_MAKER_ALL = (CaseStatus.OPEN, CaseStatus.IN_PROGRESS, CaseStatus.APPROVED)


def digest_counts(now=None, *, payment_module=None) -> dict:
    """Queue-wide counts for the digest (overdue as in the queue: due date +
    16:00 RET cutoff, RuleResultQuerySet.with_ageing). All modules by
    default; one module for the Phase 5 home page (/api/v1/<module>/summary/)."""
    now = timezone.localtime(now) if now else timezone.localtime()
    today = now.date()
    cases = ExceptionCase.objects.all()
    actions = ProposedAction.objects.all()
    if payment_module is not None:
        cases = cases.filter(payment_module=payment_module)
        actions = actions.filter(case__payment_module=payment_module)
    rows = list(cases.filter(status__in=ACTIVE_STATUSES).with_ageing(now=now)
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
    c["needs_review"] = cases.filter(needs_review=True).count()
    submitted = Counter(actions.filter(decision=Decision.PENDING).values_list("submitted_by_id", flat=True))
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
    module_id = default_module_id()
    maker_path = queue_path(module_id, mine="assigned") if module_id else ""
    checker_path = queue_path(module_id, mine="approval") if module_id else ""
    for u in makers:
        notify(AlertType.DIGEST_MAKER, [u], {
            "today": _iso(today), "name": u.get_full_name() or u.get_username(), **{k: t[k] for k in maker_fields},
            "mine": d["mine"][u.pk], "mine_overdue": d["mine_overdue"][u.pk], "link": link(maker_path),
        }, dedupe_key=f"digest:maker:{today}", link_path=maker_path)
    for u in checkers:
        notify(AlertType.DIGEST_CHECKER, [u], {
            "today": _iso(today), "name": u.get_full_name() or u.get_username(), **{k: t[k] for k in checker_fields},
            "awaiting_mine": d["submitted"][u.pk], "link": link(checker_path),
        }, dedupe_key=f"digest:checker:{today}", link_path=checker_path)
    return summary
