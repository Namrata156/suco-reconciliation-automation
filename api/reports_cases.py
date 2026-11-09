"""
Phase 6 Slice 6.3 case reports (docs/phase6_api_contract.txt B4-B6s, owner
answers D4 and "P"; design 2.4-2.6). Read-only. No priority anywhere
(owner 2026-11-08).

  GET /<module>/reports/exceptions/          cases in a business-date range (all five roles)
  GET /<module>/reports/ageing/              active cases now, rule x ageing bucket (all five roles)
  GET /<module>/reports/approvals/           maker-checker decisions on case actions (A, U, C)
  GET /<module>/reports/approvals/settings/  settings changes: codes, NTSL mapping, tolerance (A, U, C)

Case amount (D4): the matched transaction's NPCI amount, else its CBS
amount (the presence rows); a settlement case shows its current result's
unexplained amount, as the case list does. Ageing / overdue come from the
queue's own annotation (ExceptionCase.with_ageing), never recomputed here.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from urllib.parse import quote, urlencode

from django.db.models import Case, Count, DecimalField, Exists, F, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from api.base import ModuleMixin, V1APIView, module_slug
from api.export import AMOUNT, BOOL, COUNT, DATE, DATETIME, RRN, Column, Sheet, export_block, export_response, \
    range_file_base, wants_export
from api.operations import _bad_request
from api.reports import report_block, report_range, versions
from api.results import _bool_param, _csv, _enum_filter
from api.settings_views import SETTINGS_KINDS
from exception_management import api as legacy
from exception_management.actions import ActionType
from exception_management.models import (
    ACTIVE_STATUSES,
    CaseEvent,
    CaseStatus,
    CaseType,
    ChangeRequestStatus,
    Decision,
    EventType,
    ExceptionCase,
    ProposedAction,
    ResponseCodeChangeRequest,
)
from exception_management.roles import Role, user_roles
from reconciliation.models import RegisterPresence
from rule_engine.models import AgeingBucket
from settlement.models import MapStatus, NtslCategoryMapVersion, SettlementCase, SettlementTolerance

CLOSED_STATUSES = (CaseStatus.COMPLETED, CaseStatus.CLOSED_SUPERSEDED)
CLOSING_EVENTS = (EventType.COMPLETED, EventType.SUPERSEDED, EventType.CLOSED_BY_SYSTEM)
# The queue's ordering fields (api.views.ORDERING_FIELDS) without priority (owner "P").
CASE_ORDERINGS = {"business_date": "business_date", "due_date": "due_date", "age": "age_days", "status": "status",
                  "rule_code": "rule_code", "rrn": "rrn", "created_at": "created_at", "updated_at": "updated_at"}
# The queue order (exception_management.models.QUEUE_ORDER) without priority_rank.
DEFAULT_CASE_ORDER = (F("is_overdue").desc(nulls_last=True), F("due_date").asc(nulls_last=True),
                      F("age_days").desc(nulls_last=True), "business_date", "rrn", "pk")
_STATUS_LABELS = dict(CaseStatus.choices)
_DECISION_LABELS = dict(Decision.choices)
_ACTION_LABELS = dict(ActionType.choices)


def _m(v) -> str | None:
    return None if v is None else f"{v:.2f}"


def _day_bounds(d_from, d_to) -> tuple[datetime, datetime]:
    """[start of d_from, start of d_to + 1) in local time (IST)."""
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.combine(d_from, time.min), tz),
            timezone.make_aware(datetime.combine(d_to + timedelta(days=1), time.min), tz))


class ReportView(ModuleMixin, V1APIView):
    pass


class HasApprovalReportRole(BasePermission):
    message = "Only the Administrator, the Auditor and the Checker may read the approval report."

    def has_permission(self, request, view):
        return bool(user_roles(request.user) & {Role.ADMINISTRATOR, Role.AUDITOR, Role.CHECKER})


def case_amount():
    """D4 as one expression: NPCI amount, else CBS; settlement case: the
    unexplained amount of the result it is linked to."""
    def leg(name):
        return Subquery(RegisterPresence.objects.filter(match_group_id=OuterRef("match_group_id"), leg=name,
                                                        present=True).values("amount")[:1])

    settlement = Subquery(SettlementCase.objects.filter(case_id=OuterRef("pk"))
                          .values("result__unexplained_amount")[:1])
    return Case(When(case_type=CaseType.SETTLEMENT, then=settlement), default=Coalesce(leg("NPCI"), leg("CBS")),
                output_field=DecimalField(max_digits=18, decimal_places=2))


def _closed_at():
    last = (CaseEvent.objects.filter(case_id=OuterRef("pk"), event_type__in=CLOSING_EVENTS)
            .order_by("-created_at", "-id").values("created_at")[:1])
    return Case(When(status__in=CLOSED_STATUSES, then=Subquery(last)), default=Value(None))


def closed_by_system_q(case_ref: str = "pk"):
    """True for a case the system closed (no-money reply in an NPCI file, owner
    2026-11-09): it has a CLOSED_BY_SYSTEM event."""
    return Exists(CaseEvent.objects.filter(case_id=OuterRef(case_ref), event_type=EventType.CLOSED_BY_SYSTEM))


def _rule_order_key(module):
    from rule_engine.models import RuleSet, RuleSetStatus

    rs = RuleSet.objects.filter(payment_module=module, status=RuleSetStatus.ACTIVE).order_by("-activated_at").first()
    order = list(rs.rules.order_by("evaluation_order").values_list("code", flat=True)) if rs else []
    return lambda code: (order.index(code) if code in order else len(order), code)


# ===========================================================================
# B4 exception report
# ===========================================================================
def exception_queryset(module, p, errors: dict):
    """(filtered cases, order_by args, filters echo) for B4."""
    d_from, d_to = report_range(p, errors)
    qs = (ExceptionCase.objects.filter(payment_module=module).with_ageing()
          .annotate(amount=case_amount(), closed_at=_closed_at(), closed_by_system=closed_by_system_q())
          .select_related("assigned_to"))
    if d_from is not None and d_to is not None:
        qs = qs.filter(business_date__gte=d_from, business_date__lte=d_to)
    st = p.get("status") or ""
    statuses = None
    if st == "":
        qs = qs.filter(status__in=ACTIVE_STATUSES)
    elif st != "all":
        statuses = _enum_filter(p, "status", CaseStatus.values, errors)
        if statuses:
            qs = qs.filter(status__in=statuses)
    rule_codes = _csv(p["rule_code"]) if p.get("rule_code") else []
    if rule_codes:
        qs = qs.filter(rule_code__in=rule_codes)
    case_type = p.get("case_type") or None
    if case_type:
        if case_type not in CaseType.values:
            errors["case_type"] = [f"Allowed: {', '.join(CaseType.values)}"]
        else:
            qs = qs.filter(case_type=case_type)
    by_system = _bool_param(p, "closed_by_system", errors)
    if by_system is not None:
        qs = qs.filter(closed_by_system=by_system)
    overdue = _bool_param(p, "overdue", errors)
    if overdue is True:
        qs = qs.filter(is_overdue=True)
    elif overdue is False:  # a settlement case has no due date: not overdue (NULL)
        qs = qs.filter(Q(is_overdue=False) | Q(is_overdue__isnull=True))
    assigned = (p.get("assigned_to") or "").strip()
    if assigned == "none":
        qs = qs.filter(assigned_to__isnull=True)
    elif assigned:
        qs = qs.filter(assigned_to__username=assigned)
    order = DEFAULT_CASE_ORDER
    if p.get("ordering"):
        out = []
        for part in _csv(p["ordering"]):
            field = CASE_ORDERINGS.get(part.lstrip("-"))
            if field is None:
                errors["ordering"] = [f"Unknown ordering {part!r}; allowed: {', '.join(sorted(CASE_ORDERINGS))} "
                                      "(prefix - to reverse)"]
                break
            out.append(F(field).desc(nulls_last=True) if part.startswith("-") else F(field).asc(nulls_last=True))
        order = (*out, "pk")
    filters = {"date_from": d_from, "date_to": d_to, "status": "active" if st == "" else ("all" if st == "all"
                                                                                       else statuses or []),
               "rule_code": rule_codes, "case_type": case_type, "overdue": overdue, "closed_by_system": by_system,
               "assigned_to": assigned or None, "ordering": p.get("ordering") or ""}
    return qs, order, filters


def exception_rows(module, cases) -> list[dict]:
    """Rows for a page (or export chunk) of cases: two queries for the latest
    proposed action and remark of each."""
    cases = list(cases)
    ids = [c.pk for c in cases]
    actions, remarks = {}, {}
    for a in ProposedAction.objects.filter(case_id__in=ids).select_related("response_code").order_by(
            "case_id", "-submitted_at", "-id"):
        actions.setdefault(a.case_id, a)
    for e in CaseEvent.objects.filter(case_id__in=ids, event_type=EventType.REMARK).order_by(
            "case_id", "-created_at", "-id").only("id", "case_id", "remarks", "created_at"):
        remarks.setdefault(e.case_id, e.remarks)
    slug = module_slug(module)
    rows = []
    for c in cases:
        a = actions.get(c.pk)
        rows.append({
            "id": c.pk, "business_date": c.business_date, "rrn": c.rrn, "case_type": c.case_type,
            "rule_code": c.rule_code, "status_category": c.status_category, "amount": _m(c.amount),
            "status": c.status, "due_date": c.due_date, "age_days": c.age_days,
            "ageing_bucket": c.ageing_bucket or "", "is_overdue": bool(c.is_overdue),
            "assigned_to": c.assigned_to.get_username() if c.assigned_to_id else None,
            "proposed_action": None if a is None else {
                "action_type": a.action_type, "response_code": str(a.response_code) if a.response_code_id else None,
                "decision": a.decision, "reference": a.external_reference},
            "created_at": c.created_at, "closed_at": c.closed_at, "closed_by_system": bool(c.closed_by_system),
            "last_remark": remarks.get(c.pk, ""),
            "link": f"/{slug}/cases/{c.pk}",
        })
    return rows


def exception_summary(module, qs) -> dict:
    base = qs.order_by()
    counts = dict(base.values_list("status").annotate(n=Count("id")).values_list("status", "n"))
    key = _rule_order_key(module)
    by_rule = sorted(base.values("rule_code").annotate(count=Count("id"),
                                                       open=Count("id", filter=Q(status__in=ACTIVE_STATUSES))),
                     key=lambda r: key(r["rule_code"]))
    return {"by_status": [{"status": s, "count": counts[s]} for s in CaseStatus.values if counts.get(s)],
            "by_rule": [{"rule_code": r["rule_code"], "count": r["count"], "open": r["open"]} for r in by_rule]}


def _exception_sheets(module, qs, summary: dict, count: int) -> list[Sheet]:
    cols = [Column("id", "Case", COUNT), Column("business_date", "Business date", DATE), Column("rrn", "RRN", RRN),
            Column("case_type", "Case type"), Column("rule_code", "Rule"),
            Column("status_category", "Status category"), Column("amount", "Amount", AMOUNT),
            Column("status", "Status"), Column("due_date", "Due date", DATE), Column("age_days", "Age (days)", COUNT),
            Column("ageing_bucket", "Ageing bucket"), Column("is_overdue", "Overdue", BOOL),
            Column("assigned_to", "Assigned to"), Column("action_type", "Proposed action"),
            Column("response_code", "Response code"), Column("decision", "Decision"),
            Column("reference", "NPCI portal / CBS reference"), Column("created_at", "Created", DATETIME),
            Column("closed_at", "Closed", DATETIME), Column("closed_by_system", "Closed by system", BOOL),
            Column("last_remark", "Last remark")]

    def case_rows():
        from itertools import batched

        for chunk in batched(qs.iterator(chunk_size=2000), 2000):
            for r in exception_rows(module, chunk):
                a = r.pop("proposed_action") or {}
                r.update(action_type=_ACTION_LABELS.get(a.get("action_type"), a.get("action_type")),
                         response_code=a.get("response_code"),
                         decision=_DECISION_LABELS.get(a.get("decision"), a.get("decision")),
                         reference=a.get("reference"), status=_STATUS_LABELS.get(r["status"], r["status"]))
                yield r

    sum_cols = [Column("table", "Table"), Column("key", "Status / rule"), Column("count", "Cases", COUNT),
                Column("open", "Open", COUNT)]

    def summary_rows():
        for s in summary["by_status"]:
            yield {"table": "By status", "key": _STATUS_LABELS.get(s["status"], s["status"]), "count": s["count"]}
        for r in summary["by_rule"]:
            yield {"table": "By rule", "key": r["rule_code"] or "(settlement)", "count": r["count"], "open": r["open"]}

    return [Sheet("cases", "Cases", cols, case_rows, count),
            Sheet("summary", "Summary", sum_cols, summary_rows, len(summary["by_status"]) + len(summary["by_rule"]))]


class ExceptionReportView(ReportView):
    def get(self, request, module):
        p, errors = request.query_params, {}
        qs, order, filters = exception_queryset(self.module, p, errors)
        if errors:
            return _bad_request("Invalid input", errors)
        qs = qs.order_by(*order)
        summary = exception_summary(self.module, qs)
        count = qs.count()
        report = report_block("exceptions", "Exception report", self.module, filters, request.user,
                              versions(rule_sets=qs.order_by().exclude(rule_result=None)
                                       .values_list("rule_result__rule_set__version_label", flat=True).distinct()))
        sheets = _exception_sheets(self.module, qs, summary, count)
        if wants_export(request):
            return export_response(request, report=report, module=self.module, sheets=sheets,
                                   file_base=range_file_base(self.module, "exception_report", filters["date_from"],
                                                             filters["date_to"]))
        paginator = legacy.QueuePagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        response = paginator.get_paginated_response(exception_rows(self.module, page))
        response.data.update({"summary": summary, "report": report, "export": export_block(sheets)})
        return response


# ===========================================================================
# B5 ageing report (active cases, as of now)
# ===========================================================================
def _cases_link(slug: str, **query) -> str:
    q = {"status": "", **{k: v for k, v in query.items() if v}}
    return f"/{slug}/cases?{urlencode(q, quote_via=quote)}"


def ageing_report(module, *, rule_codes: list[str], case_type: str | None) -> dict:
    buckets = list(AgeingBucket.objects.filter(payment_module=module).order_by("order"))
    labels = [b.label for b in buckets]
    qs = ExceptionCase.objects.filter(payment_module=module, status__in=ACTIVE_STATUSES).with_ageing().annotate(
        amount=case_amount())
    if rule_codes:
        qs = qs.filter(rule_code__in=rule_codes)
    if case_type:
        qs = qs.filter(case_type=case_type)
    slug = module_slug(module)

    def zero():
        return {"count": 0, "amount": Decimal("0"), "overdue": 0}

    by_rule: dict[str, dict[str, dict]] = defaultdict(lambda: {b: zero() for b in labels})
    totals = {b: zero() for b in labels}
    not_aged = {"count": 0, "amount": Decimal("0"), "types": set()}
    oldest = None
    rule_sets = set()
    for c in qs.order_by().values("id", "rrn", "rule_code", "case_type", "ageing_bucket", "age_days", "is_overdue",
                                  "amount", "rule_result__rule_set__version_label").iterator():
        if c["rule_result__rule_set__version_label"]:
            rule_sets.add(c["rule_result__rule_set__version_label"])
        amount = c["amount"] or Decimal("0")
        if c["ageing_bucket"] not in labels:
            not_aged["count"] += 1
            not_aged["amount"] += amount
            not_aged["types"].add(c["case_type"])
            continue
        for cell in (by_rule[c["rule_code"]][c["ageing_bucket"]], totals[c["ageing_bucket"]]):
            cell["count"] += 1
            cell["amount"] += amount
            cell["overdue"] += 1 if c["is_overdue"] else 0
        if oldest is None or (c["age_days"], -c["id"]) > (oldest["age_days"], -oldest["id"]):
            oldest = c

    def row(cells: dict, rule: str | None) -> dict:
        out = [{"bucket": b, "count": cells[b]["count"], "amount": _m(cells[b]["amount"]),
                "overdue": cells[b]["overdue"],
                "link": _cases_link(slug, rule_code=rule or ",".join(rule_codes), ageing_bucket=b,
                                    case_type=case_type)}
               for b in labels]
        return {"cells": out, "total": {"count": sum(x["count"] for x in cells.values()),
                                        "amount": _m(sum((x["amount"] for x in cells.values()), Decimal("0"))),
                                        "overdue": sum(x["overdue"] for x in cells.values())}}

    key = _rule_order_key(module)
    only_settlement = not_aged["types"] == {CaseType.SETTLEMENT}
    return {
        "buckets": [{"label": b.label, "min_days": b.min_days, "max_days": b.max_days} for b in buckets],
        "by_rule": [{"rule_code": code, **row(by_rule[code], code)} for code in sorted(by_rule, key=key)],
        "totals": row(totals, None),
        "not_aged": {"count": not_aged["count"], "amount": _m(not_aged["amount"]),
                     "link": _cases_link(slug, case_type=CaseType.SETTLEMENT) if only_settlement else None},
        "oldest_case": None if oldest is None else {"id": oldest["id"], "rrn": oldest["rrn"],
                                                    "age_days": oldest["age_days"],
                                                    "link": f"/{slug}/cases/{oldest['id']}"},
        "_rule_sets": sorted(rule_sets),
    }


def _ageing_sheet(body: dict) -> Sheet:
    labels = [b["label"] for b in body["buckets"]]
    cols = [Column("rule_code", "Rule")]
    for b in labels:
        cols += [Column(f"{b}_count", f"{b} cases", COUNT), Column(f"{b}_amount", f"{b} amount", AMOUNT),
                 Column(f"{b}_overdue", f"{b} overdue", COUNT)]
    cols += [Column("total_count", "Total cases", COUNT), Column("total_amount", "Total amount", AMOUNT),
             Column("total_overdue", "Total overdue", COUNT)]

    def flat(name, r):
        out = {"rule_code": name, "total_count": r["total"]["count"], "total_amount": r["total"]["amount"],
               "total_overdue": r["total"]["overdue"]}
        for cell in r["cells"]:
            b = cell["bucket"]
            out.update({f"{b}_count": cell["count"], f"{b}_amount": cell["amount"], f"{b}_overdue": cell["overdue"]})
        return out

    def gen():
        for r in body["by_rule"]:
            yield flat(r["rule_code"], r)
        yield flat("Total", body["totals"])
        if body["not_aged"]["count"]:
            yield {"rule_code": "No ageing bucket (settlement cases)", "total_count": body["not_aged"]["count"],
                   "total_amount": body["not_aged"]["amount"]}

    return Sheet("by_rule", "By rule", cols, gen, len(body["by_rule"]) + 1 + (1 if body["not_aged"]["count"] else 0))


class AgeingReportView(ReportView):
    def get(self, request, module):
        p, errors = request.query_params, {}
        for name in ("date_from", "date_to"):
            if p.get(name):
                errors[name] = ["The ageing report is as of now; it takes no dates."]
        rule_codes = _csv(p["rule_code"]) if p.get("rule_code") else []
        case_type = p.get("case_type") or None
        if case_type and case_type not in CaseType.values:
            errors["case_type"] = [f"Allowed: {', '.join(CaseType.values)}"]
        if errors:
            return _bad_request("Invalid input", errors)
        body = ageing_report(self.module, rule_codes=rule_codes, case_type=case_type)
        today = timezone.localdate()
        report = report_block("ageing", "Ageing report", self.module,
                              {"as_of": today, "rule_code": rule_codes, "case_type": case_type}, request.user,
                              versions(rule_sets=body.pop("_rule_sets")))
        sheets = [_ageing_sheet(body)]
        if wants_export(request):
            return export_response(request, report=report, module=self.module, sheets=sheets,
                                   file_base=f"{self.module.code}_ageing_as_of_{today.isoformat()}")
        return Response({**body, "report": report, "export": export_block(sheets)})


# ===========================================================================
# B6 approval report (case actions) and B6s (settings changes)
# ===========================================================================
SETTINGS_DECISIONS = ("APPROVED", "REJECTED", "PENDING")
_MAP_DECISION = {MapStatus.PENDING: "PENDING", MapStatus.ACTIVE: "APPROVED", MapStatus.RETIRED: "APPROVED",
                 MapStatus.REJECTED: "REJECTED"}
_CODE_DECISION = {ChangeRequestStatus.PENDING: "PENDING", ChangeRequestStatus.APPROVED: "APPROVED",
                  ChangeRequestStatus.REJECTED: "REJECTED"}
_SETTINGS_ROUTE = {"response_code": "response-codes", "ntsl_mapping": "ntsl-mapping", "tolerance": "tolerance"}


class ApprovalFilters:
    """The filters B6 and B6s share, parsed once (dates on the decision date,
    R7; on the submission date for PENDING)."""

    def __init__(self, p, errors: dict, *, decisions):
        self.d_from, self.d_to = report_range(p, errors)
        self.decisions = _enum_filter(p, "decision", list(decisions), errors) or []
        self.checker = (p.get("checker") or "").strip()
        self.maker = (p.get("maker") or "").strip()
        self.self_approved = _bool_param(p, "self_approved", errors)
        self.action_types = _enum_filter(p, "action_type", ActionType.values, errors) or []
        self.kinds = _enum_filter(p, "kind", list(SETTINGS_KINDS), errors) or []

    def echo(self) -> dict:
        return {"date_from": self.d_from, "date_to": self.d_to, "decision": self.decisions, "checker": self.checker or None,
                "maker": self.maker or None, "self_approved": self.self_approved, "action_type": self.action_types,
                "kind": self.kinds}

    def in_range(self, decided, proposed, pending: bool) -> bool:
        ts = proposed if pending else decided
        if ts is None:
            return False
        local = timezone.localtime(ts).date()
        return self.d_from <= local <= self.d_to


def action_queryset(module, f: ApprovalFilters):
    start, end = _day_bounds(f.d_from, f.d_to)
    qs = (ProposedAction.objects.filter(case__payment_module=module)
          .filter(Q(decision=Decision.PENDING, submitted_at__gte=start, submitted_at__lt=end)
                  | (~Q(decision=Decision.PENDING) & Q(decided_at__gte=start, decided_at__lt=end)))
          .select_related("case", "response_code", "submitted_by", "decided_by")
          .annotate(case_closed_by_system=closed_by_system_q("case_id")))
    if f.decisions:
        qs = qs.filter(decision__in=f.decisions)
    if f.checker:
        qs = qs.filter(decided_by__username=f.checker)
    if f.maker:
        qs = qs.filter(submitted_by__username=f.maker)
    if f.action_types:
        qs = qs.filter(action_type__in=f.action_types)
    if f.self_approved is not None:
        qs = qs.filter(self_approved=f.self_approved)
    return qs.order_by(Coalesce("decided_at", "submitted_at").desc(), "-id")


def action_row(module, a: ProposedAction) -> dict:
    hours = None
    if a.decided_at is not None:
        hours = round((a.decided_at - a.submitted_at).total_seconds() / 3600, 1)
    return {
        "id": a.pk, "case_id": a.case_id, "business_date": a.case.business_date, "rrn": a.case.rrn,
        "rule_code": a.rule_code or a.case.rule_code, "action_type": a.action_type,
        "action_type_label": _ACTION_LABELS.get(a.action_type, a.action_type),
        "response_code": str(a.response_code) if a.response_code_id else None,
        "maker": a.submitted_by.get_username(), "submitted_at": a.submitted_at,
        "checker": a.decided_by.get_username() if a.decided_by_id else None, "decided_at": a.decided_at,
        "decision": a.decision, "self_approved": a.self_approved,
        "rejection_reason": a.decision_remarks if a.decision == Decision.REJECTED else "",
        "marked_done_at": a.completed_at, "reference": a.external_reference, "hours_to_decision": hours,
        # owner 2026-11-09: the system closed the case (no-money reply in an NPCI file); a pending
        # action was then WITHDRAWN, an approved one recorded done with the file name as reference.
        "closed_by_system": bool(getattr(a, "case_closed_by_system", False)),
        "link": f"/{module_slug(module)}/cases/{a.case_id}",
    }


def approval_summary(module, qs) -> dict:
    by_checker = (qs.order_by().exclude(decided_by=None).values("decided_by__username")
                  .annotate(approved=Count("id", filter=Q(decision=Decision.APPROVED)),
                            rejected=Count("id", filter=Q(decision=Decision.REJECTED)),
                            self_approved=Count("id", filter=Q(self_approved=True)))
                  .order_by("decided_by__username"))
    pending = ProposedAction.objects.filter(case__payment_module=module, decision=Decision.PENDING)
    oldest = pending.order_by("submitted_at", "id").first()
    slug = module_slug(module)
    return {"by_checker": [{"checker": r["decided_by__username"], "approved": r["approved"],
                            "rejected": r["rejected"], "self_approved": r["self_approved"]} for r in by_checker],
            "pending_now": pending.count(),
            "oldest_pending": None if oldest is None else {"id": oldest.pk, "submitted_at": oldest.submitted_at,
                                                           "link": f"/{slug}/cases/{oldest.case_id}"}}


def settings_rows(module, f: ApprovalFilters) -> list[dict]:
    """Every settings change of the module in the range, newest first (small
    tables: hundreds of rows at most, merged here)."""
    from api.audit import _item_labels

    slug = module_slug(module)
    rows = []
    kinds = f.kinds or list(SETTINGS_KINDS)
    if "response_code" in kinds:
        reqs = list(ResponseCodeChangeRequest.objects.filter(payment_module=module)
                    .select_related("requested_by", "decided_by"))
        labels = _item_labels("response_code", [r.pk for r in reqs])
        for r in reqs:
            rows.append({"kind": "response_code", "item_label": labels.get(r.pk, ""),
                         "proposed_by": r.requested_by.get_username(), "proposed_at": r.requested_at,
                         "decided_by": r.decided_by.get_username() if r.decided_by_id else None,
                         "decided_at": r.decided_at, "decision": _CODE_DECISION[r.status],
                         "self_approved": r.self_approved, "remarks": r.decision_remarks or r.reason})
    for kind, model, prefix in (("ntsl_mapping", NtslCategoryMapVersion, "NTSL mapping"),
                                ("tolerance", SettlementTolerance, "Tolerance")):
        if kind not in kinds:
            continue
        # Seeded versions (no proposer) were never a maker-checker decision.
        for v in model.objects.filter(payment_module=module).exclude(proposed_by=None):
            rows.append({"kind": kind, "item_label": f"{prefix} v{v.version}",
                         "proposed_by": v.proposed_by_username or None, "proposed_at": v.proposed_at,
                         "decided_by": v.decided_by_username or None, "decided_at": v.decided_at,
                         "decision": _MAP_DECISION.get(v.status, v.status), "self_approved": v.self_approved,
                         "remarks": v.decision_remarks or v.reason})
    out = []
    for r in rows:
        pending = r["decision"] == "PENDING"
        if not f.in_range(r["decided_at"], r["proposed_at"], pending):
            continue
        if f.decisions and r["decision"] not in f.decisions:
            continue
        if f.checker and r["decided_by"] != f.checker:
            continue
        if f.maker and r["proposed_by"] != f.maker:
            continue
        if f.self_approved is not None and r["self_approved"] != f.self_approved:
            continue
        r.update(kind_label=SETTINGS_KINDS[r["kind"]], link=f"/{slug}/settings/{_SETTINGS_ROUTE[r['kind']]}")
        out.append(r)
    out.sort(key=lambda r: (r["decided_at"] or r["proposed_at"]), reverse=True)
    return [{k: r[k] for k in ("kind", "kind_label", "item_label", "proposed_by", "proposed_at", "decided_by",
                               "decided_at", "decision", "self_approved", "remarks", "link")} for r in out]


def approval_sheets(module, actions_qs, settings: list[dict], summary: dict) -> list[Sheet]:
    a_cols = [Column("case_id", "Case", COUNT), Column("business_date", "Business date", DATE),
              Column("rrn", "RRN", RRN), Column("rule_code", "Rule"), Column("action_type_label", "Action"),
              Column("response_code", "Response code"), Column("maker", "Maker"),
              Column("submitted_at", "Submitted", DATETIME), Column("checker", "Checker"),
              Column("decided_at", "Decided", DATETIME), Column("decision", "Decision"),
              Column("self_approved", "Self-approved", BOOL), Column("rejection_reason", "Rejection reason"),
              Column("marked_done_at", "Marked done", DATETIME), Column("reference", "Reference"),
              Column("closed_by_system", "Case closed by system", BOOL),
              Column("hours_to_decision", "Hours to decision")]

    def action_rows():
        for a in actions_qs.iterator(chunk_size=2000):
            r = action_row(module, a)
            r["decision"] = _DECISION_LABELS.get(r["decision"], r["decision"])
            yield r

    s_cols = [Column("kind_label", "Setting"), Column("item_label", "Change"), Column("proposed_by", "Proposed by"),
              Column("proposed_at", "Proposed", DATETIME), Column("decided_by", "Decided by"),
              Column("decided_at", "Decided", DATETIME), Column("decision", "Decision"),
              Column("self_approved", "Self-approved", BOOL), Column("remarks", "Remarks")]
    sum_cols = [Column("checker", "Checker"), Column("approved", "Approved", COUNT),
                Column("rejected", "Rejected", COUNT), Column("self_approved", "Self-approved", COUNT),
                Column("pending", "Pending now (all dates)", COUNT)]

    def summary_rows():
        yield from summary["by_checker"]
        yield {"checker": "Pending now (all dates)", "pending": summary["pending_now"]}

    return [Sheet("actions", "Case actions", a_cols, action_rows, actions_qs.count()),
            Sheet("settings", "Settings changes", s_cols, lambda: iter(settings), len(settings)),
            Sheet("summary", "Summary", sum_cols, summary_rows, len(summary["by_checker"]) + 1)]


class _ApprovalView(ReportView):
    permission_classes = [IsAuthenticated, HasApprovalReportRole]
    decisions = Decision.values
    default_sheet = "actions"

    def collect(self, request):
        p, errors = request.query_params, {}
        f = ApprovalFilters(p, errors, decisions=self.decisions)
        if errors:
            return None, _bad_request("Invalid input", errors)
        return f, None

    def answer(self, request, f):
        actions_qs = action_queryset(self.module, f)
        settings = settings_rows(self.module, f)
        summary = approval_summary(self.module, actions_qs)
        report = report_block("approvals", "Approval report", self.module, f.echo(), request.user, versions())
        sheets = approval_sheets(self.module, actions_qs, settings, summary)
        if wants_export(request):
            return export_response(request, report=report, module=self.module, sheets=sheets,
                                   file_base=range_file_base(self.module, "approvals", f.d_from, f.d_to),
                                   default_sheet=self.default_sheet)
        return self.page(request, actions_qs, settings, summary, report, sheets)


class ApprovalReportView(_ApprovalView):
    def get(self, request, module):
        f, error = self.collect(request)
        return error or self.answer(request, f)

    def page(self, request, actions_qs, settings, summary, report, sheets):
        paginator = legacy.QueuePagination()
        page = paginator.paginate_queryset(actions_qs, request, view=self)
        response = paginator.get_paginated_response([action_row(self.module, a) for a in page])
        response.data.update({"summary": summary, "report": report, "export": export_block(sheets)})
        return response


class ApprovalSettingsReportView(_ApprovalView):
    decisions = SETTINGS_DECISIONS
    default_sheet = "settings"

    def get(self, request, module):
        f, error = self.collect(request)
        return error or self.answer(request, f)

    def page(self, request, actions_qs, settings, summary, report, sheets):
        paginator = legacy.QueuePagination()
        page = paginator.paginate_queryset(settings, request, view=self)
        response = paginator.get_paginated_response(page)
        response.data.update({"report": report, "export": export_block(sheets, self.default_sheet)})
        return response
