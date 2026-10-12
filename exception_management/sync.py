"""
Case sync: keeps ExceptionCase in step with the rule results of one
(payment module, business date). Runs after every evaluation
(rule_engine's evaluate_rules command and Celery task call it in the same
transaction) and from `manage.py sync_cases`. Idempotent: a second run with
nothing new writes no rows.

Which results become cases (design section 1, P1; Amendment 1 A4): every
CURRENT RuleResult with a due date, plus any that needs action but whose TAT
was not found (tat_unresolved -> case flagged no_deadline). One case per
business date + RRN.

Warnings kept on the case:
  - multiple_adjustment_rows (design section 7): >1 Adjustment row belongs to
    the RRN on this date.
  - rrc_already_sent_on (Amendment 1 A1): the earliest Adjdate of a "Refund
    Reversal Confirmation" Adjustment row for the RRN on ANY loaded date.
    Stored at sync time; because such a row can arrive with a later date's
    files, every sync re-checks this warning on the cases of ALL dates (a
    small set: only RRNs with such rows), recording a WARNINGS_CHANGED event
    when it changes.
The P5 previous-day-R12 hint is computed at read time instead
(ExceptionCaseQuerySet.with_previous_day_r12).

Re-runs (design section 6). A re-evaluation supersedes the date's results
and creates new ones; the case is keyed by date + RRN, so it survives and is
re-linked to the new result.
  - Same rule                          -> re-linked silently. If only a
                                          detail of the result changed
                                          (e.g. its due date after a holiday
                                          was added) the case is updated and
                                          a RESULT_UPDATED event records it.
  - Rule changed, not yet approved     -> case updated (rule, priority, due
    (OPEN / IN_PROGRESS / PENDING_       date, pre-filled action) + RULE_CHANGED
    APPROVAL)                             event. A PENDING_APPROVAL case goes
                                          back to IN_PROGRESS and its pending
                                          action is WITHDRAWN, so a checker
                                          never approves an action proposed
                                          under the old rule.
  - Rule changed, APPROVED / COMPLETED -> never silently changed: the case is
                                          flagged needs_review + a
                                          FLAGGED_FOR_REVIEW event (its
                                          status, assignment and approved
                                          action stay as they were; the
                                          case's rule fields show the new
                                          result, the approved action keeps
                                          the rule it was approved under).
  - No longer needs action             -> CLOSED_SUPERSEDED + SUPERSEDED
    (result has no due date, or the       event (a pending action is
    key has no current result)            withdrawn); APPROVED / COMPLETED
                                          cases are flagged instead.
  - CLOSED_SUPERSEDED case that needs  -> REOPENED: back to OPEN, unassigned.
    action again

Bulk: a fixed number of queries per date whatever the volume (results
and cases read once, bulk_create / bulk_update in batches), so a date with
~80K results is fine; only actionable results (hundreds) are read in full.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, time

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from core.models import PaymentModule
from exception_management import alerts
from exception_management.actions import default_action_for
from exception_management.models import (
    ATTENTION_RULE_CODES,
    DECIDED_STATUSES,
    CaseEvent,
    CaseStatus,
    CaseType,
    Decision,
    EventType,
    ExceptionCase,
    ProposedAction,
)
from exception_management.services import event_kwargs
from reconciliation.presence import adjustment_rows_any_date, overlay_rows_for_date
from rule_engine.models import RuleEvaluationRun, RuleResult

logger = logging.getLogger(__name__)

_BATCH = 2000
_LOOKUP_BATCH = 5000

# The case's copy of its rule result, compared on every sync.
_COPIED = ("rule_code", "status_category", "priority", "due_date", "due_cutoff_time")
_RESULT_FIELDS = _COPIED + ("no_deadline",)
_DEFAULT_FIELDS = ("default_action_type", "default_response_code_id")
_RESULT_VALUES = ("id", "match_group_id", "rrn", "response_code_id", "tat_unresolved") + _COPIED
_UPDATE_FIELDS = (
    ("rule_result_id", "match_group_id") + _RESULT_FIELDS + _DEFAULT_FIELDS
    + ("status", "assigned_to_id", "needs_review", "needs_review_reason", "result_missing",
       "multiple_adjustment_rows", "rrc_already_sent_on", "updated_at")
)

REFUND_REVERSAL_CONFIRMATION = "refund reversal confirmation"  # Adjtype, compared case-insensitively


def needs_case_q() -> Q:
    """The one definition of "this result becomes a case" (design P1, A4)."""
    return Q(due_date__isnull=False) | Q(tat_unresolved=True)


def rrc_sent_dates(payment_module: PaymentModule) -> dict[str, date]:
    """{rrn: earliest Adjdate of a Refund Reversal Confirmation row}, any loaded date (A1)."""
    out: dict[str, date] = {}
    for rrn, rows in adjustment_rows_any_date(payment_module).items():
        dates = [f.business_date for f, cells in rows
                 if (cells.get("dispute_type") or "").strip().lower() == REFUND_REVERSAL_CONFIRMATION]
        if dates:
            out[rrn] = min(dates)
    return out


@dataclass
class SyncSummary:
    payment_module_code: str
    business_date: date
    actionable_results: int = 0
    created: int = 0
    unchanged: int = 0
    relinked: int = 0
    result_updated: int = 0
    rule_changed: int = 0
    flagged_for_review: int = 0
    superseded: int = 0
    reopened: int = 0
    actions_withdrawn: int = 0
    adjustment_flag_changed: int = 0
    rrc_warning_changed: int = 0
    events_written: int = 0
    cases_by_rule_status: dict = field(default_factory=dict)
    multiple_adjustment_rows: int = 0
    rrc_already_sent: int = 0
    no_deadline: int = 0

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["business_date"] = self.business_date.isoformat()
        return d


def _json(value):
    if isinstance(value, (date, time)):
        return value.isoformat()
    return value


def _values_from_result(r: dict) -> dict:
    action, code_id = default_action_for(r["rule_code"], r["response_code_id"])
    values = {f: r[f] for f in _COPIED}
    values.update(
        no_deadline=bool(r["tat_unresolved"]) and r["due_date"] is None,
        rule_result_id=r["id"],
        match_group_id=r["match_group_id"],
        default_action_type=action,
        default_response_code_id=code_id,
    )
    return values


def _diff(case: ExceptionCase, values: dict) -> dict:
    return {
        f: [_json(getattr(case, f)), _json(values[f])]
        for f in _RESULT_FIELDS + _DEFAULT_FIELDS
        if getattr(case, f) != values[f]
    }


def _apply(case: ExceptionCase, values: dict) -> None:
    for f, v in values.items():
        setattr(case, f, v)


def _flag(case: ExceptionCase, reason: str) -> None:
    case.needs_review = True
    case.needs_review_reason = f"{case.needs_review_reason}\n{reason}".strip() if case.needs_review_reason else reason


def _chunks(items, size):
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start : start + size]


def sync_cases_for_date(payment_module: PaymentModule, business_date: date, *, source: str = "") -> SyncSummary:
    summary = SyncSummary(payment_module.code, business_date)
    with transaction.atomic():
        now = timezone.now()
        current = RuleResult.objects.current().filter(payment_module=payment_module, business_date=business_date)
        actionable = {r["rrn"]: r for r in current.filter(needs_case_q()).values(*_RESULT_VALUES)}
        summary.actionable_results = len(actionable)

        # Transaction cases only: settlement cases (no RRN) belong to settlement.cases.
        cases = {
            c.rrn: c
            for c in ExceptionCase.objects.select_for_update(of=("self",)).filter(
                payment_module=payment_module, business_date=business_date, case_type=CaseType.TRANSACTION
            )
        }
        for c in cases.values():
            c.payment_module = payment_module  # cached for the event copies; no per-case query

        # Current (non-actionable) results for existing cases that are no longer actionable.
        others: dict[str, dict] = {}
        lapsed_groups = [c.match_group_id for rrn, c in cases.items() if rrn not in actionable]
        for batch in _chunks(lapsed_groups, _LOOKUP_BATCH):
            others.update({r["rrn"]: r for r in current.filter(match_group_id__in=batch).values(*_RESULT_VALUES)})

        multi_adjustment = {
            rrn for rrn, rows in overlay_rows_for_date(payment_module, business_date).items() if len(rows) > 1
        }
        rrc_dates = rrc_sent_dates(payment_module)

        pending_actions = {
            a.case_id: a
            for a in ProposedAction.objects.select_for_update(of=("self",))
            .select_related("response_code", "submitted_by")
            .filter(case__in=[c for c in cases.values() if c.status == CaseStatus.PENDING_APPROVAL],
                    decision=Decision.PENDING)
        }
        withdrawn: list[ProposedAction] = []
        events: list[dict] = []
        changed: list[ExceptionCase] = []
        flagged: list[ExceptionCase] = []
        became_r07x: list[ExceptionCase] = []  # existing cases whose rule changed TO R07x in this run

        def withdraw(case: ExceptionCase, why: str) -> ProposedAction | None:
            action = pending_actions.pop(case.pk, None)
            if action is not None:
                action.decision = Decision.WITHDRAWN
                action.decided_at = now
                action.decision_remarks = why
                withdrawn.append(action)
            return action

        def event(case, event_type, *, old_status="", changes=None, action=None, remarks=""):
            events.append(event_kwargs(
                case, event_type, old_status=old_status or case.status, new_status=case.status,
                changes=changes, action=action, remarks=remarks, source=source,
            ))

        # -- new cases -------------------------------------------------------
        new_cases = []
        for rrn, r in actionable.items():
            if rrn in cases:
                continue
            values = _values_from_result(r)
            new_cases.append(ExceptionCase(
                payment_module=payment_module, business_date=business_date, rrn=rrn,
                status=CaseStatus.OPEN, multiple_adjustment_rows=rrn in multi_adjustment,
                rrc_already_sent_on=rrc_dates.get(rrn), **values,
            ))

        # -- existing cases ----------------------------------------------------
        for rrn, case in cases.items():
            old_status = case.status
            dirty = False
            r = actionable.get(rrn)
            if r is not None:
                values = _values_from_result(r)
                changes = _diff(case, values)
                if case.result_missing:
                    changes["result_missing"] = [True, False]
                    case.result_missing = False
                relinked = case.rule_result_id != values["rule_result_id"]
                rule_changed = "rule_code" in changes
                old_rule = case.rule_code
                _apply(case, values)
                if case.rule_code in ATTENTION_RULE_CODES and old_rule not in ATTENTION_RULE_CODES:
                    became_r07x.append(case)  # owner 2026-10-11: alert on change too, not only creation
                if old_status == CaseStatus.CLOSED_SUPERSEDED:
                    case.status = CaseStatus.OPEN
                    if case.assigned_to_id:
                        changes["assigned_to_id"] = [case.assigned_to_id, None]
                    case.assigned_to = None
                    event(case, EventType.REOPENED, old_status=old_status, changes=changes)
                    summary.reopened += 1
                elif rule_changed and old_status in DECIDED_STATUSES:
                    _flag(case, f"Re-evaluation changed the rule from {old_rule} to {case.rule_code} after the "
                                f"case was {old_status} ({now:%Y-%m-%d %H:%M})")
                    event(case, EventType.FLAGGED_FOR_REVIEW, old_status=old_status, changes=changes,
                          remarks=case.needs_review_reason)
                    summary.flagged_for_review += 1
                    flagged.append(case)
                elif rule_changed:
                    action = None
                    if old_status == CaseStatus.PENDING_APPROVAL:
                        action = withdraw(case, f"Withdrawn: the rule changed from {old_rule} to {case.rule_code}")
                        case.status = CaseStatus.IN_PROGRESS
                    event(case, EventType.RULE_CHANGED, old_status=old_status, changes=changes,
                          action=action)
                    summary.rule_changed += 1
                elif changes:
                    event(case, EventType.RESULT_UPDATED, old_status=old_status, changes=changes)
                    summary.result_updated += 1
                elif relinked:
                    summary.relinked += 1
                dirty = bool(changes) or relinked
            else:
                r = others.get(rrn)
                if r is None:
                    changes = {} if case.result_missing else {"result_missing": [False, True]}
                    case.result_missing = True
                    relinked = False
                else:
                    values = _values_from_result(r)
                    changes = _diff(case, values)
                    if case.result_missing:
                        changes["result_missing"] = [True, False]
                        case.result_missing = False
                    relinked = case.rule_result_id != values["rule_result_id"]
                    _apply(case, values)
                if changes:
                    now_what = "no current rule result" if r is None else f"{r['rule_code']} (no due date)"
                    if old_status == CaseStatus.CLOSED_SUPERSEDED:
                        event(case, EventType.RESULT_UPDATED, old_status=old_status, changes=changes)
                        summary.result_updated += 1
                    elif old_status in DECIDED_STATUSES:
                        _flag(case, f"Re-evaluation: no longer needs action, now {now_what}, after the case was "
                                    f"{old_status} ({now:%Y-%m-%d %H:%M})")
                        event(case, EventType.FLAGGED_FOR_REVIEW, old_status=old_status, changes=changes,
                              remarks=case.needs_review_reason)
                        summary.flagged_for_review += 1
                        flagged.append(case)
                    else:
                        action = withdraw(case, f"Withdrawn: the case no longer needs action (now {now_what})")
                        case.status = CaseStatus.CLOSED_SUPERSEDED
                        event(case, EventType.SUPERSEDED, old_status=old_status, changes=changes, action=action,
                              remarks=f"No longer needs action: now {now_what}")
                        summary.superseded += 1
                elif relinked:
                    summary.relinked += 1
                dirty = bool(changes) or relinked

            multi = rrn in multi_adjustment
            if case.multiple_adjustment_rows != multi:
                case.multiple_adjustment_rows = multi
                event(case, EventType.ADJUSTMENT_ROWS_CHANGED, changes={"multiple_adjustment_rows": [not multi, multi]})
                summary.adjustment_flag_changed += 1
                dirty = True
            if dirty:
                case.updated_at = now
                changed.append(case)
            else:
                summary.unchanged += 1

        # -- writes ------------------------------------------------------------
        if new_cases:
            ExceptionCase.objects.bulk_create(new_cases, batch_size=_BATCH)
            for case in new_cases:
                events.append(event_kwargs(
                    case, EventType.CREATED, new_status=case.status, source=source,
                    changes={f: [None, _json(getattr(case, f))] for f in _RESULT_FIELDS + _DEFAULT_FIELDS
                             + ("multiple_adjustment_rows", "rrc_already_sent_on")},
                ))
            summary.created = len(new_cases)
        if changed:
            ExceptionCase.objects.bulk_update(changed, _UPDATE_FIELDS, batch_size=_BATCH)
        if withdrawn:
            ProposedAction.objects.bulk_update(withdrawn, ["decision", "decided_at", "decision_remarks"], batch_size=_BATCH)
            summary.actions_withdrawn = len(withdrawn)
        if events:
            CaseEvent.objects.bulk_create([CaseEvent(**kw) for kw in events], batch_size=_BATCH)
        summary.events_written = len(events)
        summary.rrc_warning_changed, n = _refresh_rrc_warnings(payment_module, rrc_dates, now, source)
        summary.events_written += n

        # Alerts (written on commit): one per kind per sync run.
        # R07x: cases new as R07x plus existing cases that became R07x -- one summary;
        # a case already R07x before this run is not alerted again.
        alerts.r07x_cases_created(
            business_date, [c for c in new_cases if c.rule_code in ATTENTION_RULE_CODES] + became_r07x
        )
        alerts.cases_flagged_for_review(business_date, flagged)

        date_cases = ExceptionCase.objects.filter(
            payment_module=payment_module, business_date=business_date, case_type=CaseType.TRANSACTION
        )
        summary.cases_by_rule_status = {
            f"{row['rule_code']}/{row['status']}": row["n"]
            for row in date_cases.values("rule_code", "status").annotate(n=Count("id")).order_by("rule_code", "status")
        }
        summary.multiple_adjustment_rows = date_cases.filter(multiple_adjustment_rows=True).count()
        summary.rrc_already_sent = date_cases.filter(rrc_already_sent_on__isnull=False).count()
        summary.no_deadline = date_cases.filter(no_deadline=True).count()
    if summary.flagged_for_review:
        logger.warning("%s %s: %d case(s) flagged for review after re-evaluation", payment_module.code,
                       business_date, summary.flagged_for_review)
    return summary


def _refresh_rrc_warnings(payment_module, rrc_dates: dict, now, source: str) -> tuple[int, int]:
    """Brings rrc_already_sent_on up to date on every case of every date
    (A1); a new case got it at creation. Returns (cases changed, events)."""
    stale = [
        c for c in ExceptionCase.objects.select_for_update(of=("self",)).filter(
            payment_module=payment_module, case_type=CaseType.TRANSACTION
        ).filter(
            Q(rrn__in=list(rrc_dates)) | Q(rrc_already_sent_on__isnull=False)
        )
        if c.rrc_already_sent_on != rrc_dates.get(c.rrn)
    ]
    events = []
    for c in stale:
        c.payment_module = payment_module
        old, c.rrc_already_sent_on, c.updated_at = c.rrc_already_sent_on, rrc_dates.get(c.rrn), now
        events.append(CaseEvent(**event_kwargs(
            c, EventType.WARNINGS_CHANGED, old_status=c.status, new_status=c.status, source=source,
            changes={"rrc_already_sent_on": [_json(old), _json(c.rrc_already_sent_on)]}, remarks=c.rrc_warning,
        )))
    if stale:
        ExceptionCase.objects.bulk_update(stale, ["rrc_already_sent_on", "updated_at"], batch_size=_BATCH)
        CaseEvent.objects.bulk_create(events, batch_size=_BATCH)
    return len(stale), len(events)


def evaluated_business_dates(payment_module: PaymentModule) -> list[date]:
    """Dates that have been evaluated at least once (small table)."""
    return list(
        RuleEvaluationRun.objects.filter(payment_module=payment_module)
        .order_by("business_date")
        .values_list("business_date", flat=True)
        .distinct()
    )
