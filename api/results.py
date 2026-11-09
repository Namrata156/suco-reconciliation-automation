"""
/api/v1/<module>/ reconciliation results and settlement (Phase 5 Slice 3;
design docs/phase5_design_draft_v1.txt 4.12-4.15). Read-only, all five
roles. Thin views over data the Phase 2-4 services already wrote:

  reconciliation.presence  -> TransactionMatchGroup / RegisterPresence
  rule_engine.evaluator    -> RuleEvaluationRun (rule_counts) / RuleResult
  settlement.selfcheck     -> NtslCycleCheck (check_ntsl)
  settlement.reconcile     -> SettlementResult (+ cycle rows, snapshot)
  settlement.cases         -> SettlementCase

  GET /dates/<date>/results/summary/          counts per rule / status / priority (rule_counts)
  GET /dates/<date>/results/                  match groups + current rule result (LARGE, paginated)
  GET /dates/<date>/groups/<id>/              one group: legs, overlay rows, rule results, case
  GET /dates/<date>/settlement/               current result, NTSL self-check per cycle, case
  GET /dates/<date>/settlement/transactions/  contributing transactions of the current result
  GET /cases/<id>/settlement/transactions/    the same for a settlement case's result

Nothing here computes a rule, a G rule or a settlement figure; it only lists
what is stored. About 70,000 groups per date: only one page is ever read.

UI refinements U3 (docs/ui_refinements_api_contract.txt B3, B4): the
settlement "summary" (the plain sentence's figures, "explained" by type)
and the transactions kind "unexplained" (every entry no rule explains).
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Case, Count, Exists, F, FilteredRelation, OuterRef, Prefetch, Q, Value, When
from django.db.models import IntegerField
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response

from api.base import ModuleMixin, V1APIView, module_slug
from api.operations import _DateMixin, _bad_request
from exception_management import api as legacy
from exception_management.detail import date_overlay_rows, settlement_result_payload
from exception_management.models import _PRIORITY_RANK, ACTIVE_STATUSES, CaseType, ExceptionCase
from reconciliation.models import ProcessingStatus as GroupStatus
from reconciliation.models import RegisterPresence, TransactionMatchGroup
from reconciliation.presence import DEFAULT_OVERLAY_LEGS, DEFAULT_PRESENCE_LEGS
from rule_engine.models import Priority, RuleEvaluationRun, RuleResult, StatusCategory
from exception_management.actions import ACTION_SPECS, DEFAULT_ACTION_BY_RULE
from settlement import gap_rules
from settlement.models import CheckStatus, NtslCycleCheck, ResultStatus, SettlementResult, Side

CBS, NPCI = "CBS", "NPCI"


def _m(v) -> str | None:
    return None if v is None else f"{v:.2f}"


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _bool_param(p, name, errors) -> bool | None:
    v = p.get(name)
    if v in (None, ""):
        return None
    if v not in ("true", "false"):
        errors[name] = ["Allowed: true, false"]
        return None
    return v == "true"


def _enum_filter(p, name, allowed, errors) -> list[str] | None:
    if not p.get(name):
        return None
    wanted = _csv(p[name])
    bad = [v for v in wanted if v not in allowed]
    if bad:
        errors[name] = [f"Unknown {', '.join(bad)}; allowed: {', '.join(allowed)}"]
        return None
    return wanted


class ResultsView(_DateMixin, ModuleMixin, V1APIView):
    pass


def _groups(module, d):
    return TransactionMatchGroup.objects.filter(payment_module=module, business_date=d)


def _no_results(module, d) -> Response | None:
    if _groups(module, d).exists() or RuleEvaluationRun.objects.filter(payment_module=module, business_date=d).exists():
        return None
    return Response({"detail": "No reconciliation results for this date."}, status=status.HTTP_404_NOT_FOUND)


def _latest_run(module, d):
    return (RuleEvaluationRun.objects.filter(payment_module=module, business_date=d).select_related("rule_set")
            .order_by("-started_at", "-id").first())


def _run_payload(run) -> dict | None:
    if run is None:
        return None
    return {"id": run.pk, "rule_set": run.rule_set.version_label, "started_at": run.started_at,
            "finished_at": run.finished_at, "group_count": run.group_count,
            "superseded_count": run.superseded_count, "calendar_incomplete_count": run.calendar_incomplete_count,
            "triggered_by": run.triggered_by}


# ---------------------------------------------------------------------------
# 4.12 summary
# ---------------------------------------------------------------------------
class ResultSummaryView(ResultsView):
    """Counts per rule from the latest RuleEvaluationRun.rule_counts (each run
    supersedes the date's earlier results, so these are the current ones),
    grouped by the rule's own status category and priority in that run's
    rule set; plus the matrix's processing-status counts and the cases."""

    def get(self, request, module, business_date):
        d, error = self.business_date(business_date)
        if error:
            return error
        missing = _no_results(self.module, d)
        if missing:
            return missing
        run = _latest_run(self.module, d)
        rules, by_status, by_priority = [], {}, {}
        if run is not None:
            known = {r.code: r for r in run.rule_set.rules.order_by("evaluation_order")}
            order = list(known) + sorted(set(run.rule_counts) - set(known))
            for code in order:
                n = run.rule_counts.get(code, 0)
                r = known.get(code)
                rules.append({"rule_code": code, "name": r.name if r else "", "count": n,
                              "status_category": r.status_category if r else None,
                              "priority": r.priority if r else None})
                if r is not None:
                    by_status[r.status_category] = by_status.get(r.status_category, 0) + n
                    by_priority[r.priority] = by_priority.get(r.priority, 0) + n
        groups = _groups(self.module, d).order_by()
        g = groups.aggregate(
            total=Count("id", filter=Q(is_superseded=False)), superseded=Count("id", filter=Q(is_superseded=True)),
            on_us=Count("id", filter=Q(is_superseded=False, is_on_us=True)),
            cbs_reversed=Count("id", filter=Q(is_superseded=False, cbs_reversed=True)),
            **{f"s_{s}": Count("id", filter=Q(is_superseded=False, processing_status=s)) for s in GroupStatus.values},
        )
        cases = ExceptionCase.objects.filter(payment_module=self.module, business_date=d).order_by().aggregate(
            total=Count("id"), active=Count("id", filter=Q(status__in=ACTIVE_STATUSES)))
        return Response({
            "business_date": d,
            "rule_evaluation": _run_payload(run),
            "rules": rules,
            "by_status_category": [{"value": v, "label": label, "count": by_status[v]}
                                   for v, label in StatusCategory.choices if v in by_status],
            "by_priority": [{"value": v, "label": label, "count": by_priority[v]}
                            for v, label in Priority.choices if v in by_priority],
            "results_total": sum(run.rule_counts.values()) if run else 0,
            "match_groups": {
                "total": g["total"], "superseded": g["superseded"], "on_us": g["on_us"],
                "cbs_reversed": g["cbs_reversed"],
                "by_processing_status": {s: g[f"s_{s}"] for s in GroupStatus.values},
            },
            "cases": cases,
        })


# ---------------------------------------------------------------------------
# 4.12 list
# ---------------------------------------------------------------------------
PRESENCE_PATTERNS = {
    "CBS_AND_NPCI": "CBS and NPCI",
    "CBS_ONLY": "CBS only",
    "NPCI_ONLY": "NPCI only",
    "NEITHER": "Neither (overlay rows only)",
}

_RANK = Case(*(When(r_priority=p, then=Value(n)) for p, n in _PRIORITY_RANK.items()),
             default=Value(len(_PRIORITY_RANK)), output_field=IntegerField())

RESULT_ORDERING = {
    "rrn": "rrn",
    "rule_code": "r_rule_code",
    "status_category": "r_status_category",
    "priority": "r_priority_rank",  # HIGH first
    "processing_status": "processing_status",
    "due_date": "r_due_date",
}


def _ordering(value):
    if not value:
        return ("rrn", "pk"), None
    out = []
    for part in _csv(value):
        field = RESULT_ORDERING.get(part.lstrip("-"))
        if field is None:
            return None, (f"Unknown ordering {part!r}; allowed: {', '.join(sorted(RESULT_ORDERING))} "
                          "(prefix - to reverse)")
        out.append(F(field).desc(nulls_last=True) if part.startswith("-") else F(field).asc(nulls_last=True))
    return (*out, "pk"), None


def _present(leg):
    return Exists(RegisterPresence.objects.filter(match_group=OuterRef("pk"), leg=leg, present=True))


def _with_current_result(qs):
    """LEFT JOIN on the current RuleResult (at most one per group, DB constraint)."""
    return qs.annotate(cur=FilteredRelation("rule_results", condition=Q(rule_results__is_superseded=False))).annotate(
        r_id=F("cur__id"), r_rule_code=F("cur__rule_code"), r_status_category=F("cur__status_category"),
        r_priority=F("cur__priority"), r_due_date=F("cur__due_date"), r_due_cutoff_time=F("cur__due_cutoff_time"),
    ).annotate(r_priority_rank=_RANK)


def _leg(presences: dict, name: str) -> dict:
    p = presences.get(name)
    if p is None or not p.present:
        return {"present": False, "amount": None, "direction": None, "response_code": None}
    return {"present": True, "amount": _m(p.amount), "direction": p.direction or None,
            "response_code": p.response_code or None}


def _rule_result_row(g) -> dict | None:
    if g.r_id is None:
        return None
    return {"id": g.r_id, "rule_code": g.r_rule_code, "status_category": g.r_status_category,
            "priority": g.r_priority, "due_date": g.r_due_date, "due_cutoff_time": g.r_due_cutoff_time}


def _cases_for(module, d, groups) -> dict[int, dict]:
    """{match group id: case summary} for the page's transaction cases (one
    case per module + date + RRN, as one group)."""
    by_rrn = {g.rrn: g.pk for g in groups}
    out = {}
    for c in ExceptionCase.objects.filter(payment_module=module, business_date=d, case_type=CaseType.TRANSACTION,
                                          rrn__in=list(by_rrn)).select_related("assigned_to"):
        out[by_rrn[c.rrn]] = {"id": c.pk, "status": c.status, "priority": c.priority, "rule_code": c.rule_code,
                    "assigned_to": c.assigned_to.get_username() if c.assigned_to else None}
    return out


def filtered_results(module, d, p, errors: dict):
    """(queryset, order_by args) for the results list filters below
    (ResultListView); errors gets the 400 messages. Shared by the screen and
    its download (Phase 6 B8), so both always list the same rows."""
    qs = _with_current_result(_groups(module, d))
    superseded = _bool_param(p, "superseded", errors)
    qs = qs.filter(is_superseded=bool(superseded))
    for name, field, allowed in (("rule_code", "r_rule_code", None),
                                 ("status_category", "r_status_category", StatusCategory.values),
                                 ("priority", "r_priority", Priority.values),
                                 ("processing_status", "processing_status", GroupStatus.values)):
        if allowed is None:
            values = _csv(p[name]) if p.get(name) else None
        else:
            values = _enum_filter(p, name, allowed, errors)
        if values:
            qs = qs.filter(**{f"{field}__in": values})
    if p.get("rrn"):
        qs = qs.filter(rrn=p["rrn"].strip())
    presence = p.get("presence")
    if presence:
        if presence not in PRESENCE_PATTERNS:
            errors["presence"] = [f"Allowed: {', '.join(PRESENCE_PATTERNS)}"]
        else:
            cbs, npci = _present(CBS), _present(NPCI)
            qs = qs.filter({"CBS_AND_NPCI": cbs & npci, "CBS_ONLY": cbs & ~npci, "NPCI_ONLY": ~cbs & npci,
                            "NEITHER": ~cbs & ~npci}[presence])
    for name, field in (("on_us", "is_on_us"), ("reversed", "cbs_reversed")):
        v = _bool_param(p, name, errors)
        if v is not None:
            qs = qs.filter(**{field: v})
    evaluated = _bool_param(p, "evaluated", errors)
    if evaluated is not None:
        qs = qs.filter(r_id__isnull=not evaluated)
    order, order_error = _ordering(p.get("ordering"))
    if order_error:
        errors["ordering"] = [order_error]
    return qs, order


RESULT_FILTERS = ("rule_code", "status_category", "priority", "processing_status", "rrn", "presence", "on_us",
                  "reversed", "superseded", "evaluated", "ordering")
# The download takes the same filters minus priority (owner 2026-11-08 "P": no priority in any report;
# cleanup 2026-11-09). The screen list keeps accepting it (unchanged API).
EXPORT_FILTERS = tuple(f for f in RESULT_FILTERS if f != "priority")
NO_PRIORITY = "The results download has no priority (removed from every report, owner 2026-11-08)."


def refuse_priority(p, errors: dict) -> None:
    """400s for a priority filter or ordering on the download."""
    if p.get("priority"):
        errors["priority"] = [NO_PRIORITY]
    if any(part.lstrip("-") == "priority" for part in _csv(p.get("ordering") or "")):
        errors["ordering"] = [NO_PRIORITY]


class ResultListView(ResultsView):
    """Filters: rule_code, status_category, priority, processing_status
    (comma lists), rrn (exact), presence (PRESENCE_PATTERNS), on_us,
    reversed, superseded (default false), evaluated (has a current rule
    result). ordering: RESULT_ORDERING, default rrn. ?export=csv|xlsx: the
    whole filtered list as a file (Phase 6 B8, api.export); the download
    refuses priority (filter or ordering) with a 400."""

    def get(self, request, module, business_date):
        d, error = self.business_date(business_date)
        if error:
            return error
        missing = _no_results(self.module, d)
        if missing:
            return missing
        p, errors = request.query_params, {}
        qs, order = filtered_results(self.module, d, p, errors)
        if "export" in p:
            refuse_priority(p, errors)
        if errors:
            return _bad_request("Invalid input", errors)
        if "export" in p:
            return results_export(request, self.module, d, qs.order_by(*order))
        qs = qs.select_related("settlement_cycle").prefetch_related(
            Prefetch("register_presences", queryset=RegisterPresence.objects.only(
                "id", "match_group_id", "leg", "present", "amount", "direction", "response_code")))
        paginator = legacy.QueuePagination()
        page = paginator.paginate_queryset(qs.order_by(*order), request, view=self)
        cases = _cases_for(self.module, d, page)
        rows = []
        for g in page:
            legs = {x.leg: x for x in g.register_presences.all()}
            rows.append({
                "id": g.pk, "rrn": g.rrn, "business_date": g.business_date,
                "settlement_cycle": g.settlement_cycle.cycle_code if g.settlement_cycle else None,
                "processing_status": g.processing_status,
                "cbs": _leg(legs, CBS), "npci": _leg(legs, NPCI),
                "overlays": [leg for leg in DEFAULT_OVERLAY_LEGS if leg in legs and legs[leg].present],
                "amount_match": g.amount_match, "direction_match": g.direction_match,
                "is_on_us": g.is_on_us, "cbs_reversed": g.cbs_reversed, "is_superseded": g.is_superseded,
                "rule_result": _rule_result_row(g),
                "case": cases.get(g.pk),
            })
        return paginator.get_paginated_response(rows)


# ---------------------------------------------------------------------------
# Phase 6 B8: one date's results as a file (no priority column: owner 2026-11-08 "P")
# ---------------------------------------------------------------------------
_EXPORT_CHUNK = 2000
_GROUP_STATUS_LABELS = dict(GroupStatus.choices)
_CATEGORY_LABELS = dict(StatusCategory.choices)


def _result_columns():
    from api.export import AMOUNT, BOOL, COUNT, DATE, RRN, Column

    return [Column("rrn", "RRN", RRN), Column("business_date", "Business date", DATE),
            Column("settlement_cycle", "Cycle"), Column("processing_status", "Processing status"),
            Column("cbs_present", "In CBS", BOOL), Column("cbs_amount", "CBS amount", AMOUNT),
            Column("cbs_direction", "CBS direction"),
            Column("npci_present", "In NPCI", BOOL), Column("npci_amount", "NPCI amount", AMOUNT),
            Column("npci_direction", "NPCI direction"), Column("npci_response_code", "NPCI response code", RRN),
            Column("overlays", "Dispute rows"), Column("amount_match", "Amount match", BOOL),
            Column("direction_match", "Direction match", BOOL), Column("is_on_us", "On-us", BOOL),
            Column("cbs_reversed", "CBS reversed", BOOL), Column("rule_code", "Rule"),
            Column("status_category", "Status category"), Column("due_date", "Due date", DATE),
            Column("case_id", "Case", COUNT), Column("case_status", "Case status"),
            Column("assigned_to", "Assigned to")]


def _result_export_rows(module, d, qs):
    """Streams the rows: one server-side cursor over the groups, plus one
    presence and one case query per chunk of 2,000."""
    from itertools import batched

    from exception_management.models import CaseStatus

    _CASE_STATUS_LABELS = dict(CaseStatus.choices)
    fields = ("id", "rrn", "business_date", "settlement_cycle__cycle_code", "processing_status", "amount_match",
              "direction_match", "is_on_us", "cbs_reversed", "r_rule_code", "r_status_category", "r_due_date")
    for chunk in batched(qs.values(*fields).iterator(chunk_size=_EXPORT_CHUNK), _EXPORT_CHUNK):
        legs: dict[int, dict] = {}
        for x in RegisterPresence.objects.filter(match_group_id__in=[g["id"] for g in chunk], present=True).values(
                "match_group_id", "leg", "amount", "direction", "response_code"):
            legs.setdefault(x["match_group_id"], {})[x["leg"]] = x
        cases = {c["rrn"]: c for c in ExceptionCase.objects.filter(
            payment_module=module, business_date=d, case_type=CaseType.TRANSACTION,
            rrn__in=[g["rrn"] for g in chunk]).values("rrn", "id", "status", "assigned_to__username")}
        for g in chunk:
            gl, case = legs.get(g["id"], {}), cases.get(g["rrn"])
            cbs, npci = gl.get(CBS), gl.get(NPCI)
            yield {"rrn": g["rrn"], "business_date": g["business_date"],
                   "settlement_cycle": g["settlement_cycle__cycle_code"],
                   "processing_status": _GROUP_STATUS_LABELS.get(g["processing_status"], g["processing_status"]),
                   "cbs_present": cbs is not None, "cbs_amount": cbs["amount"] if cbs else None,
                   "cbs_direction": (cbs["direction"] or None) if cbs else None,
                   "npci_present": npci is not None, "npci_amount": npci["amount"] if npci else None,
                   "npci_direction": (npci["direction"] or None) if npci else None,
                   "npci_response_code": (npci["response_code"] or None) if npci else None,
                   "overlays": ", ".join(leg for leg in DEFAULT_OVERLAY_LEGS if leg in gl),
                   "amount_match": g["amount_match"], "direction_match": g["direction_match"],
                   "is_on_us": g["is_on_us"], "cbs_reversed": g["cbs_reversed"], "rule_code": g["r_rule_code"],
                   "status_category": _CATEGORY_LABELS.get(g["r_status_category"], g["r_status_category"]),
                   "due_date": g["r_due_date"], "case_id": case["id"] if case else None,
                   "case_status": _CASE_STATUS_LABELS.get(case["status"], case["status"]) if case else None,
                   "assigned_to": case["assigned_to__username"] if case else None}


def results_export(request, module, d, qs):
    from api.export import Sheet, export_response
    from api.reports import report_block, versions

    p = request.query_params
    run = _latest_run(module, d)
    filters = {"business_date": d, **{k: p[k] for k in EXPORT_FILTERS if p.get(k)}}
    filters.setdefault("superseded", "false")
    report = report_block("results", f"Reconciliation results for {d:%d-%b-%Y}", module, filters, request.user,
                          versions(rule_sets=[run.rule_set.version_label] if run else []))
    sheet = Sheet("results", "Results", _result_columns(), lambda: _result_export_rows(module, d, qs), qs.count())
    return export_response(request, report=report, module=module, sheets=[sheet],
                           file_base=f"{module.code}_results_{d.isoformat()}")


# ---------------------------------------------------------------------------
# 4.13 match group detail
# ---------------------------------------------------------------------------
def _fact(f) -> dict | None:
    if f is None:
        return None
    return {"id": f.pk, "register_type": f.source_register_type, "business_date": f.business_date,
            "amount": _m(f.amount), "txn_amount": _m(f.txn_amount), "direction": f.direction,
            "settlement_cycle": f.settlement_cycle.cycle_code if f.settlement_cycle else None,
            "uploaded_file_id": f.uploaded_file_id, "filename": f.uploaded_file.filename if f.uploaded_file else None,
            "row_reference": f.row_reference}


def _full_rule_result(r: RuleResult) -> dict:
    return {"id": r.pk, "rule_code": r.rule_code, "rule_name": r.rule.name, "rule_set": r.rule_set.version_label,
            "run_id": r.run_id, "status_category": r.status_category, "priority": r.priority, "action": r.action,
            "response_code": str(r.response_code) if r.response_code else None, "reason": r.reason,
            "tat_start_date": r.tat_start_date, "tat_label": r.tat_label, "tat_unresolved": r.tat_unresolved,
            "due_date": r.due_date, "due_cutoff_time": r.due_cutoff_time,
            "calendar_incomplete": r.calendar_incomplete, "rbi_compensation": r.rbi_compensation,
            "facts": r.facts, "evaluated_at": r.evaluated_at, "is_superseded": r.is_superseded,
            "superseded_at": r.superseded_at}


class GroupDetailView(ResultsView):
    def get(self, request, module, business_date, pk):
        d, error = self.business_date(business_date)
        if error:
            return error
        g = get_object_or_404(_groups(self.module, d).select_related("settlement_cycle"), pk=pk)
        fact_rel = ("transaction_record__uploaded_file", "transaction_record__settlement_cycle",
                    "paired_transaction_record__uploaded_file", "paired_transaction_record__settlement_cycle")
        legs = []
        order = list(DEFAULT_PRESENCE_LEGS) + list(DEFAULT_OVERLAY_LEGS)
        presences = sorted(g.register_presences.select_related(*fact_rel),
                           key=lambda x: (order.index(x.leg) if x.leg in order else len(order), x.leg))
        for x in presences:
            legs.append({
                "leg": x.leg, "kind": "presence" if x.leg in DEFAULT_PRESENCE_LEGS else "overlay",
                "present": x.present, "amount": _m(x.amount), "direction": x.direction or None,
                "response_code": x.response_code or None, "ssk_side": x.ssk_side, "dispute_type": x.dispute_type,
                "original_not_loaded": x.original_not_loaded,
                "fact": _fact(x.transaction_record), "paired_fact": _fact(x.paired_transaction_record),
            })
        results = (RuleResult.objects.filter(match_group=g).select_related("rule", "rule_set", "response_code")
                   .order_by("is_superseded", "-evaluated_at", "-id"))
        current = next((r for r in results if not r.is_superseded), None)
        case = (ExceptionCase.objects.filter(payment_module=self.module, business_date=d,
                                             case_type=CaseType.TRANSACTION)
                .filter(Q(match_group=g) | Q(rrn=g.rrn)).select_related("assigned_to").order_by("-id").first())
        return Response({
            "id": g.pk, "rrn": g.rrn, "business_date": g.business_date,
            "settlement_cycle": g.settlement_cycle.cycle_code if g.settlement_cycle else None,
            "processing_status": g.processing_status,
            "flags": {"amount_match": g.amount_match, "direction_match": g.direction_match,
                      "status_match": g.status_match, "cbs_reversed": g.cbs_reversed, "is_on_us": g.is_on_us,
                      "is_superseded": g.is_superseded},
            "superseded_at": g.superseded_at,
            "legs": legs,
            # Every dispute row of the date for the RRN; the presence row above holds only the first per leg.
            "overlay_rows": date_overlay_rows(self.module, d, g.rrn),
            "rule_result": _full_rule_result(current) if current else None,
            "earlier_rule_results": [_full_rule_result(r) for r in results if r.is_superseded],
            "case": None if case is None else {
                "id": case.pk, "status": case.status, "priority": case.priority, "rule_code": case.rule_code,
                "assigned_to": case.assigned_to.get_username() if case.assigned_to else None},
            "created_at": g.created_at, "updated_at": g.updated_at,
        })


# ---------------------------------------------------------------------------
# 4.14 settlement per date and cycle, S2 self-check
# ---------------------------------------------------------------------------
def _check_row(c: NtslCycleCheck) -> dict:
    """One cycle of the S2 NTSL self-check (settlement.selfcheck), as stored
    on NtslCycleCheck:

      failures        [str]  plain-language reasons; [] on PASS;
                             ["No NTSL lines loaded for this cycle"] on MISSING
      unmapped_lines  [{"description": str, "row_reference": str,
                        "txn_count": int | None, "debit": "0.00", "credit": "0.00"}]
                             NTSL lines no mapping entry matched (amounts are
                             2-decimal strings)
      category_totals {<NtslCategory value>: {"lines": int, "txn_count": int,
                        "debit": "0.00", "credit": "0.00"}}
                             only categories that had lines

    The four net blocks carry {"ntsl", "computed", "difference"} as 2-decimal
    strings or None.

    Fee GST check (settlement.fee_gst, owner 2026-11-06), WARNING ONLY -- it
    never changes status / failures:
      gst_warning     bool   some row below is not within
      gst_check       None (not checked: MISSING cycle, or stored before the
                      check existed) or {"rate": "0.18", "tolerance":
                      "1.00" (flat, per pair and side), "rows": [
                        {"fee_category": "SWITCHING_FEE", "gst_category":
                         "SWITCHING_FEE_GST", "side": "DEBIT" | "CREDIT",
                         "fee": "0.00", "fee_txn_count": int (information),
                         "expected_gst": "0.00", "ntsl_gst": "0.00",
                         "difference": "0.00" (expected - NTSL),
                         "tolerance": "0.00", "within": bool,
                         "note": None | "OUTSIDE_TOLERANCE" |
                                 "GST_WITHOUT_FEE" | "FEE_WITHOUT_GST"}]}
                      12 rows: 6 pairs x 2 sides, always in the same order."""
    return {"id": c.pk, "cycle": c.settlement_cycle.cycle_code, "status": c.status, "failures": c.failures,
            "map_version": c.map_version.version, "line_count": c.line_count, "unmapped_count": c.unmapped_count,
            "unmapped_lines": c.unmapped_lines, "category_totals": c.category_totals,
            "subtotal_debit": {"ntsl": _m(c.ntsl_subtotal_debit), "computed": _m(c.computed_subtotal_debit),
                               "difference": _m(c.subtotal_debit_diff)},
            "subtotal_credit": {"ntsl": _m(c.ntsl_subtotal_credit), "computed": _m(c.computed_subtotal_credit),
                                "difference": _m(c.subtotal_credit_diff)},
            "settlement_net": {"ntsl": _m(c.ntsl_settlement_net), "computed": _m(c.computed_settlement_net),
                               "difference": _m(c.settlement_diff)},
            "final_net": {"ntsl": _m(c.ntsl_final_net), "computed": _m(c.computed_final_net),
                          "difference": _m(c.final_diff)},
            "gst_warning": c.gst_warning, "gst_check": c.gst_check or None,
            "checked_at": c.checked_at, "source": c.source}


def self_check_payload(module, d) -> dict:
    checks = list(NtslCycleCheck.objects.filter(payment_module=module, business_date=d, is_current=True)
                  .select_related("settlement_cycle", "map_version").order_by("settlement_cycle__start_time",
                                                                              "settlement_cycle_id"))
    counts = {s: sum(1 for c in checks if c.status == s) for s in CheckStatus.values}
    return {"cycles": len(checks), **{s.lower(): n for s, n in counts.items()},
            "all_pass": bool(checks) and counts[CheckStatus.PASS] == len(checks),
            # Fee GST check, warning only: number of cycles with a gst_warning (not part of all_pass).
            "gst_warnings": sum(1 for c in checks if c.gst_warning),
            "results": [_check_row(c) for c in checks]}


TRANSACTION_KINDS = {
    # kind: (label, where in SettlementResult.snapshot, field names of each row)
    "npci": ("NTSL-settled NPCI rows other than G01 (G02-G06)", ("contributing",), None),
    "sop": ("SOP check items: the RRN's CBS net minus its NTSL net (G07, G08, R06b...)", ("sop", "items"),
            ["rrn", "rule", "g_rule", "amount"]),  # settlement.reconcile sop_rows
    "adjustments": ("Money-moving Adjustment rows assigned to DC1 / DC2 (G09)", ("g09", "rows"), None),
    "z": ("Rule results that move no NTSL money (Z)", ("z", "items"), None),
}
_FORMAT_KEY = {"npci": ("contributing_format",), "adjustments": ("g09", "row_format"), "z": ("z", "item_format")}
TRANSACTION_FILTERS = ("rrn", "rule", "g_rule", "cycle", "side")
# UI refinements 5.3: one merged list of every entry no rule explains (contract B4).
UNEXPLAINED_KIND = "unexplained"
UNEXPLAINED_FIELDS = ["source", "source_label", "rrn", "rule", "g_rule", "cycle", "side", "amount", "detail"]
UNEXPLAINED_SOURCES = {
    "NPCI_NOT_ATTRIBUTED": "NTSL-settled NPCI row no rule explains",
    "NPCI_NO_DIRECTION": "NTSL-settled NPCI row with no debit / credit",
    "ADJUSTMENT_NOT_SIGNED": "Adjustment row not in the G09 sign table",
    "ADJUSTMENT_NOT_ASSIGNED": "Adjustment row not assigned to DC1 / DC2",
    "NTSL_LINE_NOT_COVERED": "NTSL line no G rule covers",
}
_CONTRIBUTING_FORMAT = ["rrn", "g_rule", "rule", "cycle", "side", "amount", "npci_code", "role"]
_G09_FORMAT = ["rrn", "adjtype", "remitter", "beneficiary", "amount", "raised_at"]


def _u(source, *, rrn=None, rule=None, g_rule=None, cycle=None, side=None, amount=None, detail="") -> dict:
    return {"source": source, "source_label": UNEXPLAINED_SOURCES[source], "rrn": rrn or None, "rule": rule or None,
            "g_rule": g_rule or None, "cycle": cycle, "side": side, "amount": amount, "detail": detail}


def unexplained_entries(snapshot: dict) -> list[dict]:
    """Every entry of a settlement result that no rule explains, from its
    snapshot (settlement.reconcile), in one shape. Never recomputed."""
    out = []
    fmt = snapshot.get("contributing_format") or _CONTRIBUTING_FORMAT
    for raw in snapshot.get("contributing") or []:
        r = dict(zip(fmt, raw))
        if r.get("g_rule") == gap_rules.UNATTRIBUTED:
            out.append(_u("NPCI_NOT_ATTRIBUTED", rrn=r.get("rrn"), rule=r.get("rule"), g_rule=r.get("g_rule"),
                          cycle=r.get("cycle"), side=r.get("side"), amount=r.get("amount"),
                          detail=f"NTSL settled this NPCI row (code {r.get('npci_code') or '-'}); "
                                 f"rule {r.get('rule') or 'none'} is not explained by any G rule."))
    for raw in snapshot.get("not_placed") or []:
        r = dict(zip(fmt, raw))
        out.append(_u("NPCI_NO_DIRECTION", rrn=r.get("rrn"), rule=r.get("rule"), g_rule=r.get("g_rule"),
                      cycle=r.get("cycle"), amount=r.get("amount"),
                      detail="The NPCI row has no debit / credit direction, so it has no side."))
    g09 = snapshot.get("g09") or {}
    for raw in g09.get("not_in_sign_table") or []:
        r = dict(zip(_G09_FORMAT, raw))
        out.append(_u("ADJUSTMENT_NOT_SIGNED", rrn=r.get("rrn"), g_rule=gap_rules.G09, side=Side.ADJUSTMENT,
                      amount=r.get("amount"),
                      detail=f"Adjustment type '{r.get('adjtype')}' (remitter {r.get('remitter')}, beneficiary "
                             f"{r.get('beneficiary')}) is not in the G09 sign table, or SSK is on both / neither side."))
    for raw in g09.get("unassigned") or []:
        r = dict(zip(_G09_FORMAT + ["signed"], raw))
        out.append(_u("ADJUSTMENT_NOT_ASSIGNED", rrn=r.get("rrn"), g_rule=gap_rules.G09, side=Side.ADJUSTMENT,
                      amount=r.get("signed") or r.get("amount"),
                      detail=f"Adjustment '{r.get('adjtype')}' raised at {r.get('raised_at') or 'an unknown time'}: "
                             "not on this business date, so not in DC1 / DC2."))
    for row in snapshot.get("cycles") or []:
        if row.get("side") != Side.UNCOVERED:
            continue
        for line in (row.get("detail") or {}).get("lines") or []:
            amount = Decimal(str(line.get("credit") or 0)) - Decimal(str(line.get("debit") or 0))
            out.append(_u("NTSL_LINE_NOT_COVERED", cycle=row.get("cycle"), side=Side.UNCOVERED,
                          amount=f"{amount:.2f}",
                          detail=f"NTSL line '{line.get('description')}' ({line.get('category')})."))
    return out


# -- "explained" by type (contract B3) -------------------------------------------
EXPLAINED_TYPES = {
    "DRC": "DRC (debited, not reversed)",
    "TCC": "TCC",
    "RET": "RET (timeout, not credited)",
    "RRC": "RRC (adjustment already reversed in CBS)",
    "LATE_ADJUSTMENTS": "Adjustments in the dispute cycles (DC1 / DC2)",
    "OTHER": "Other explained items",
}


# Owner Q3 (2026-10-27, waiting on ops): the G09 dispute-cycle Adjustment rows
# are shown as the "late adjustments" type of the breakdown. If ops' tally sheet
# says otherwise, set this to False: they then leave explained_by_type and are
# returned as summary["notes"]["late_adjustments"] instead (same figures).
LATE_ADJUSTMENTS_IN_BREAKDOWN = True


def _explained_type(rule: str) -> str:
    action = DEFAULT_ACTION_BY_RULE.get(rule)
    flag = ACTION_SPECS[action].code_flag if action in ACTION_SPECS else None
    return flag if flag in ("DRC", "TCC", "RET", "RRC") else "OTHER"


def explained_by_type(snapshot: dict) -> list[dict]:
    """SOP check items (G04-G08) grouped by the rule's reply flag, plus the
    G09 dispute-cycle Adjustment rows (in_sop_check false)."""
    buckets: dict[str, dict] = {}

    def add(type_, rule, g_rule, amount, in_sop):
        b = buckets.setdefault(type_, {"type": type_, "label": EXPLAINED_TYPES[type_], "count": 0,
                                       "amount": Decimal("0"), "in_sop_check": in_sop, "rules": {}})
        r = b["rules"].setdefault((rule, g_rule), {"rule": rule, "g_rule": g_rule,
                                                   "g_label": gap_rules.G_LABELS.get(g_rule, ""), "count": 0,
                                                   "amount": Decimal("0")})
        for x in (b, r):
            x["count"] += 1
            x["amount"] += amount

    for rrn, rule, g_rule, amount in ((snapshot.get("sop") or {}).get("items") or []):
        add(_explained_type(rule), rule, g_rule, Decimal(str(amount)), True)
    if LATE_ADJUSTMENTS_IN_BREAKDOWN:
        for amount in _late_adjustment_amounts(snapshot):
            add("LATE_ADJUSTMENTS", None, gap_rules.G09, amount, False)
    out = []
    for type_ in EXPLAINED_TYPES:
        b = buckets.get(type_)
        if b is None:
            continue
        rules = [{**r, "amount": f"{r['amount']:.2f}"} for r in b["rules"].values()]
        out.append({**b, "amount": f"{b['amount']:.2f}", "rules": rules})
    return out


def _late_adjustment_amounts(snapshot: dict) -> list[Decimal]:
    """Signed amounts of the G09 Adjustment rows assigned to DC1 / DC2."""
    g09 = snapshot.get("g09") or {}
    fmt = g09.get("row_format") or (_G09_FORMAT + ["cycle", "signed"])
    return [Decimal(str(dict(zip(fmt, raw)).get("signed") or 0)) for raw in g09.get("rows") or []]


def _notes(snapshot: dict) -> dict:
    if LATE_ADJUSTMENTS_IN_BREAKDOWN:
        return {}
    amounts = _late_adjustment_amounts(snapshot)
    return {"late_adjustments": {"label": EXPLAINED_TYPES["LATE_ADJUSTMENTS"], "count": len(amounts),
                                 "amount": f"{sum(amounts, Decimal('0')):.2f}"}} if amounts else {}


def settlement_summary(module, result: SettlementResult | None, checks: dict, link) -> dict | None:
    """The figures of the plain sentence on top of the settlement page
    (contract B3). Read from the stored result; nothing is recomputed."""
    if result is None:
        return None
    snapshot = result.snapshot or {}
    sop = snapshot.get("sop") or {}
    by_type = explained_by_type(snapshot) if result.status != ResultStatus.NOT_READY else []
    invalid = sop.get("cbs_rows_without_valid_rrn") or {}
    return {
        "status": result.status, "status_label": ResultStatus(result.status).label,
        "tallied": result.status == ResultStatus.TALLIED, "reasons": result.reasons,
        "cbs_net": _m(result.sop_cbs_net), "ntsl_net": _m(result.sop_ntsl_net),
        "difference": _m(result.sop_difference), "explained": _m(result.sop_explained),
        "unexplained": _m(result.sop_unexplained),
        "cycles_unexplained_amount": _m(result.unexplained_amount),
        "cycles_unexplained_count": result.unexplained_count,
        "unexplained_entries": len(unexplained_entries(snapshot)),
        "explained_by_type": by_type,
        "notes": _notes(snapshot) if result.status != ResultStatus.NOT_READY else {},
        "cbs_rows_without_valid_rrn": ({"count": invalid.get("count", 0), "amount": invalid.get("amount")}
                                       if invalid else None),
        "self_check_failed": bool(checks["cycles"]) and not checks["all_pass"],
        "settlement_case": None if link is None else {
            "id": link.pk, "status": link.status, "link": f"/{module_slug(module)}/cases/{link.pk}"},
    }


def _dig(snapshot: dict, path: tuple):
    v = snapshot
    for key in path:
        v = v.get(key) if isinstance(v, dict) else None
    return v


def settlement_transactions(view, request, result: SettlementResult | None) -> Response:
    """The contributing transactions stored in the result's snapshot (design
    4.15), one kind at a time, filtered and paginated."""
    p, errors = request.query_params, {}
    kind = p.get("kind") or "npci"
    if kind not in TRANSACTION_KINDS and kind != UNEXPLAINED_KIND:
        errors["kind"] = [f"Allowed: {', '.join([*TRANSACTION_KINDS, UNEXPLAINED_KIND])}"]
    if p.get("side") and p["side"] not in Side.values:
        errors["side"] = [f"Allowed: {', '.join(Side.values)}"]
    only_unexplained = _bool_param(p, "unexplained", errors)
    if errors:
        return _bad_request("Invalid input", errors)
    snapshot = result.snapshot if result is not None else {}
    if kind == UNEXPLAINED_KIND:
        fields = UNEXPLAINED_FIELDS
        rows = unexplained_entries(snapshot)
    else:
        _label, path, fields = TRANSACTION_KINDS[kind]
        fields = fields or _dig(snapshot, _FORMAT_KEY[kind]) or []
        rows = [dict(zip(fields, r)) for r in (_dig(snapshot, path) or [])]
        if only_unexplained and kind == "npci":  # UI refinements 5.3
            rows = [r for r in rows if r.get("g_rule") == gap_rules.UNATTRIBUTED]
    for name in TRANSACTION_FILTERS:
        if p.get(name) and (not fields or name in fields):
            wanted = set(_csv(p[name]))
            rows = [r for r in rows if str(r.get(name)) in wanted]
    paginator = legacy.QueuePagination()
    page = paginator.paginate_queryset(rows, request, view=view)
    response = paginator.get_paginated_response(page)
    response.data["kind"] = kind
    response.data["fields"] = fields
    response.data["result_id"] = result.pk if result else None
    response.data["result_status"] = result.status if result else None
    response.data["counts"] = {k: len(_dig(snapshot, v[1]) or []) for k, v in TRANSACTION_KINDS.items()}
    response.data["counts"][UNEXPLAINED_KIND] = len(unexplained_entries(snapshot))
    return response


def _current_result(module, d):
    return (SettlementResult.objects.filter(payment_module=module, business_date=d, is_current=True)
            .select_related("map_version", "tolerance").first())


class SettlementView(ResultsView):
    def get(self, request, module, business_date):
        d, error = self.business_date(business_date)
        if error:
            return error
        result = _current_result(self.module, d)
        checks = self_check_payload(self.module, d)
        if result is None and not checks["cycles"]:
            return Response({"detail": "No settlement result or NTSL self-check for this date."},
                            status=status.HTTP_404_NOT_FOUND)
        body = None
        if result is not None:
            body = settlement_result_payload(result)
            body.update({
                "map_version": result.map_version.version if result.map_version else None,
                "tolerance_version": result.tolerance.version if result.tolerance else None,
                "rule_run_id": result.rule_run_id, "source": result.source,
                "unexplained_amount": _m(result.unexplained_amount), "unexplained_count": result.unexplained_count,
                "transaction_counts": {k: len(_dig(result.snapshot, v[1]) or [])
                                       for k, v in TRANSACTION_KINDS.items()},
            })
        link = (ExceptionCase.objects.filter(payment_module=self.module, business_date=d,
                                             case_type=CaseType.SETTLEMENT).select_related("assigned_to").first())
        summary = settlement_summary(self.module, result, checks, link)
        earlier = (SettlementResult.objects.filter(payment_module=self.module, business_date=d, is_current=False)
                   .order_by("-computed_at", "-id").values("id", "status", "computed_at", "superseded_at",
                                                           "unexplained_amount")[:20])
        return Response({
            "business_date": d,
            "summary": summary,
            "result": body,
            "self_check": checks,
            "case": None if link is None else {
                "id": link.pk, "status": link.status, "priority": link.priority, "needs_review": link.needs_review,
                "assigned_to": link.assigned_to.get_username() if link.assigned_to else None},
            "earlier_results": [{**e, "unexplained_amount": _m(e["unexplained_amount"])} for e in earlier],
        })


class SettlementTransactionsView(ResultsView):
    def get(self, request, module, business_date):
        d, error = self.business_date(business_date)
        if error:
            return error
        result = _current_result(self.module, d)
        if result is None:
            return Response({"detail": "No settlement result for this date."}, status=status.HTTP_404_NOT_FOUND)
        return settlement_transactions(self, request, result)


class CaseSettlementTransactionsView(ModuleMixin, V1APIView):
    """The contributing transactions of the result a settlement case is about
    (design 4.15). 404 for a transaction case."""

    def get(self, request, module, pk):
        case = get_object_or_404(ExceptionCase.objects.filter(payment_module=self.module,
                                                              case_type=CaseType.SETTLEMENT)
                                 .select_related("settlement_case__result"), pk=pk)
        link = getattr(case, "settlement_case", None)
        if link is None:
            return Response({"detail": "This settlement case is not linked to a result."},
                            status=status.HTTP_404_NOT_FOUND)
        return settlement_transactions(self, request, link.result)
