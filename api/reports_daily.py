"""
Phase 6 Slice 6.2 per-date reports (docs/phase6_api_contract.txt B1-B3;
design 2.1-2.3). Read-only, all five roles (reports.view). Each report's
query lives in one function (reconciliation_rows, settlement_rows,
volume_rows) so history tables can be added there once archival is built
(design 6).

  GET /<module>/reports/reconciliation/   one row per business date (date_overviews + stored rule counts)
  GET /<module>/reports/settlement/       one row per business date (current SettlementResult)
  GET /<module>/reports/volume/           per day or month, file type (and cycle), from DailyVolume

Every answer carries "report" (RR2) and "export" (RR3); ?export=csv|xlsx
returns the file (api.export).
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Count, Q

from api.base import ModuleMixin, V1APIView, module_slug
from api.export import AMOUNT, BOOL, COUNT, DATE, DATETIME, Column, Sheet, export_block, export_response, \
    range_file_base, wants_export
from api.operations import _bad_request
from api.reports import MAX_RANGE_MONTHS, ReportDatePagination, report_block, report_range, versions
from api.results import _bool_param, _enum_filter
from core.models import RegisterType, SettlementCycle
from exception_management import api as legacy
from exception_management.models import CaseType, ExceptionCase
from file_processing.models import FileTypeConfig
from reconciliation.models import ProcessingStatus as GroupStatus
from reconciliation.overview import _latest_uploads, business_dates, date_overviews
from rule_engine.models import Rule, StatusCategory
from settlement.models import CheckStatus, NtslCycleCheck, ResultStatus, SettlementCycleResult, SettlementResult, Side
from transactions.models import DailyVolume, DailyVolumeDate, VolumeSide

ORDERINGS = ("-business_date", "business_date")
NONE_STATUS = "NONE"  # B2: no settlement result yet


def _m(v) -> str | None:
    return None if v is None else f"{v:.2f}"


def _dates_in(module, d_from: date, d_to: date) -> list[date]:
    """The /dates/ list in the range, newest first."""
    return [d for d in business_dates(module) if d_from <= d <= d_to]


def _ordering(p, errors) -> str:
    ordering = p.get("ordering") or "-business_date"
    if ordering not in ORDERINGS:
        errors["ordering"] = [f"Allowed: {', '.join(ORDERINGS)}"]
    return ordering


def _spans_months(d_from: date, d_to: date) -> bool:
    return (d_from.year, d_from.month) != (d_to.year, d_to.month)


class ReportView(ModuleMixin, V1APIView):
    pass


# ===========================================================================
# B1 daily reconciliation report = "Results (all dates)"
# ===========================================================================
def _empty_groups() -> dict:
    return {"total": 0, "by_status": {s: 0 for s in GroupStatus.values}, "on_us": 0, "cbs_reversed": 0}


def _rule_info(rule_set_ids) -> tuple[dict, list[str]]:
    """({rule set id: {code: status_category}}, codes in rule order, newest rule set first)."""
    info: dict[int, dict[str, str]] = defaultdict(dict)
    order: list[str] = []
    rules = (Rule.objects.filter(rule_set_id__in=rule_set_ids).order_by("-rule_set_id", "evaluation_order")
             .values_list("rule_set_id", "code", "status_category"))
    for rs, code, cat in rules:
        info[rs][code] = cat
        if code not in order:
            order.append(code)
    return info, order


def reconciliation_rows(module, dates: list[date]) -> dict:
    """Rows for `dates` (in the given order) plus what the totals need."""
    # One pass over the live match groups: date_overviews counts them by status as well (group_breakdown).
    overviews = date_overviews(module, dates, group_breakdown=True)
    evaluations = {d: overviews[d]["_evaluation"] for d in dates}
    info, rule_order = _rule_info({ev.rule_set_id for ev in evaluations.values() if ev is not None})
    slug = module_slug(module)
    rows = []
    for d in dates:
        ov, ev = overviews[d], evaluations[d]
        counts = dict(ev.rule_counts or {}) if ev is not None else {}
        cats = {c: 0 for c in StatusCategory.values}
        for code, n in counts.items():
            cat = info.get(ev.rule_set_id, {}).get(code)
            if cat is not None:
                cats[cat] += n
        f, st, run = ov["files"], ov["settlement"], ov["last_run"]
        has_results = ev is not None or ov["matrix"]["built"]
        rows.append({
            "business_date": d,
            "files": {"expected": f["expected"], "present": f["expected"] - f["missing"], "missing": f["missing"],
                      "received": f["files_received"]},
            "match_groups": ov["_groups"],
            "results_by_category": cats,
            "rule_counts": counts,
            "cases": {"created": ov["cases"]["total"], "open": ov["cases"]["active"]},
            "settlement": None if st is None else {"status": st["status"],
                                                   "unexplained_amount": st["unexplained_amount"]},
            "rule_evaluation": None if ev is None else {
                "id": ev.pk, "rule_set": ev.rule_set.version_label, "finished_at": ev.finished_at,
                "triggered_by": ev.triggered_by},
            "last_run": None if run is None else {"id": run["id"], "status": run["status"],
                                                  "requested_by": run["requested_by"],
                                                  "finished_at": run["finished_at"]},
            "links": {"results": f"/{slug}/dates/{d.isoformat()}/results" if has_results else None,
                      "settlement": f"/{slug}/dates/{d.isoformat()}/settlement" if st is not None else None,
                      "date": f"/{slug}/dates/{d.isoformat()}"},
        })
    used = {c for r in rows for c, n in r["rule_counts"].items() if n}
    rule_codes = [c for c in rule_order if c in used] + sorted(used - set(rule_order))
    rule_sets = [ev.rule_set.version_label for ev in evaluations.values() if ev is not None]
    return {"rows": rows, "rule_codes": rule_codes, "rule_sets": rule_sets}


def reconciliation_totals(rows: list[dict]) -> dict:
    t = {"dates": len(rows),
         "files": {"expected": 0, "present": 0, "missing": 0, "received": 0},
         "match_groups": _empty_groups(),
         "results_by_category": {c: 0 for c in StatusCategory.values},
         "rule_counts": {},
         "cases": {"created": 0, "open": 0},
         "settlement": {"by_status": {**{s: 0 for s in ResultStatus.values}, NONE_STATUS: 0},
                        "unexplained_amount": Decimal("0")}}
    for r in rows:
        for k in t["files"]:
            t["files"][k] += r["files"][k]
        g = r["match_groups"]
        for k in ("total", "on_us", "cbs_reversed"):
            t["match_groups"][k] += g[k]
        for s, n in g["by_status"].items():
            t["match_groups"]["by_status"][s] += n
        for c, n in r["results_by_category"].items():
            t["results_by_category"][c] += n
        for code, n in r["rule_counts"].items():
            t["rule_counts"][code] = t["rule_counts"].get(code, 0) + n
        for k in t["cases"]:
            t["cases"][k] += r["cases"][k]
        st = r["settlement"]
        t["settlement"]["by_status"][st["status"] if st else NONE_STATUS] += 1
        if st and st["unexplained_amount"] is not None:
            t["settlement"]["unexplained_amount"] += Decimal(st["unexplained_amount"])
    t["settlement"]["unexplained_amount"] = _m(t["settlement"]["unexplained_amount"])
    return t


def _months(rows: list[dict], with_totals) -> list[dict]:
    by_month: dict[str, list] = {}
    for r in rows:
        by_month.setdefault(r["business_date"].strftime("%Y-%m"), []).append(r)
    return [{"month": m, **with_totals(rs)} for m, rs in by_month.items()]


_CATEGORY_LABELS = dict(StatusCategory.choices)
_STATUS_LABELS = dict(GroupStatus.choices)


def _reconciliation_sheets(data: dict, rows: list[dict], totals: dict, months: list[dict]) -> list[Sheet]:
    cols = [Column("business_date", "Business date", DATE),
            Column("files_expected", "Files expected", COUNT), Column("files_present", "Files present", COUNT),
            Column("files_missing", "Files missing", COUNT), Column("files_received", "Files received", COUNT),
            Column("groups_total", "Transactions (match groups)", COUNT)]
    cols += [Column(f"g_{s}", _STATUS_LABELS[s], COUNT) for s in GroupStatus.values]
    cols += [Column("on_us", "On-us", COUNT), Column("cbs_reversed", "CBS reversed", COUNT)]
    cols += [Column(f"c_{c}", _CATEGORY_LABELS[c], COUNT) for c in StatusCategory.values]
    cols += [Column("cases_created", "Cases created", COUNT), Column("cases_open", "Cases open", COUNT),
             Column("settlement_status", "Settlement status"),
             Column("unexplained_amount", "Settlement unexplained amount", AMOUNT),
             Column("rule_set", "Rule set"), Column("evaluated_at", "Rule evaluation finished", DATETIME),
             Column("run_status", "Last run status"), Column("run_by", "Last run started by"),
             Column("run_finished_at", "Last run finished", DATETIME)]

    def flat(label, r) -> dict:
        g = r["match_groups"]
        out = {"business_date": label, "files_expected": r["files"]["expected"],
               "files_present": r["files"]["present"], "files_missing": r["files"]["missing"],
               "files_received": r["files"]["received"], "groups_total": g["total"], "on_us": g["on_us"],
               "cbs_reversed": g["cbs_reversed"], "cases_created": r["cases"]["created"],
               "cases_open": r["cases"]["open"]}
        out.update({f"g_{s}": n for s, n in g["by_status"].items()})
        out.update({f"c_{c}": n for c, n in r["results_by_category"].items()})
        st = r["settlement"]
        if "by_status" in (st or {}):  # a totals row
            out["unexplained_amount"] = st["unexplained_amount"]
        elif st:
            out.update(settlement_status=st["status"], unexplained_amount=st["unexplained_amount"])
        ev, run = r.get("rule_evaluation"), r.get("last_run")
        if ev:
            out.update(rule_set=ev["rule_set"], evaluated_at=ev["finished_at"])
        if run:
            out.update(run_status=run["status"], run_by=run["requested_by"], run_finished_at=run["finished_at"])
        return out

    def date_rows():
        for r in rows:
            yield flat(r["business_date"], r)
        for m in months:
            yield flat(f"Total {m['month']}", m)
        yield flat("Total", totals)

    codes = data["rule_codes"]
    rule_cols = [Column("business_date", "Business date", DATE)] + [Column(c, c, COUNT) for c in codes]

    def rule_rows():
        for r in rows:
            yield {"business_date": r["business_date"], **{c: r["rule_counts"].get(c, 0) for c in codes}}
        yield {"business_date": "Total", **{c: totals["rule_counts"].get(c, 0) for c in codes}}

    extra = len(months) + 1
    return [Sheet("dates", "Dates", cols, date_rows, len(rows) + extra),
            Sheet("rules", "Rules by date", rule_cols, rule_rows, len(rows) + 1)]


class ReconciliationReportView(ReportView):
    def get(self, request, module):
        p, errors = request.query_params, {}
        d_from, d_to = report_range(p, errors)
        open_only = _bool_param(p, "open_cases_only", errors)
        ordering = _ordering(p, errors)
        if errors:
            return _bad_request("Invalid input", errors)
        dates = _dates_in(self.module, d_from, d_to)
        if ordering == "business_date":
            dates.reverse()
        data = reconciliation_rows(self.module, dates)
        rows = data["rows"]
        if open_only:
            rows = [r for r in rows if r["cases"]["open"]]
        totals = reconciliation_totals(rows)
        months = _months(rows, reconciliation_totals) if _spans_months(d_from, d_to) else []
        report = report_block("reconciliation", "Daily reconciliation report", self.module,
                              {"date_from": d_from, "date_to": d_to, "open_cases_only": bool(open_only),
                               "ordering": ordering}, request.user, versions(rule_sets=data["rule_sets"]))
        sheets = _reconciliation_sheets(data, rows, totals, months)
        if wants_export(request):
            return export_response(request, report=report, module=self.module, sheets=sheets,
                                   file_base=range_file_base(self.module, "reconciliation", d_from, d_to))
        paginator = ReportDatePagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        response = paginator.get_paginated_response(page)
        response.data.update({"rule_codes": data["rule_codes"], "totals": totals, "months": months,
                              "report": report, "export": export_block(sheets)})
        return response


# ===========================================================================
# B2 settlement report = "Settlement (all dates)"
# ===========================================================================
SETTLEMENT_STATUSES = [*ResultStatus.values, NONE_STATUS]


def settlement_rows(module, dates: list[date]) -> tuple[list[dict], dict]:
    results = {r.business_date: r for r in SettlementResult.objects.filter(
        payment_module=module, business_date__in=dates, is_current=True)
        .select_related("map_version", "tolerance", "rule_run__rule_set").defer("snapshot")}
    checks = {r["business_date"]: r for r in NtslCycleCheck.objects.filter(
        payment_module=module, business_date__in=dates, is_current=True).order_by().values("business_date")
        .annotate(total=Count("id"), passed=Count("id", filter=Q(status=CheckStatus.PASS)))}
    cases = {}
    for c in ExceptionCase.objects.filter(payment_module=module, business_date__in=dates,
                                          case_type=CaseType.SETTLEMENT).order_by("id"):
        cases[c.business_date] = c
    slug = module_slug(module)
    rows = []
    for d in dates:
        r, ck, case = results.get(d), checks.get(d), cases.get(d)
        sop = None
        if r is not None and r.sop_cbs_net is not None:
            sop = {"cbs_net": _m(r.sop_cbs_net), "ntsl_net": _m(r.sop_ntsl_net), "difference": _m(r.sop_difference),
                   "explained": _m(r.sop_explained), "unexplained": _m(r.sop_unexplained)}
        rows.append({
            "business_date": d,
            "status": r.status if r else None,
            "status_label": ResultStatus(r.status).label if r else None,
            "reasons": list(r.reasons or []) if r else [],
            "computed_at": r.computed_at if r else None,
            "self_check": {"cycles_pass": ck["passed"] if ck else 0, "cycles_total": ck["total"] if ck else 0},
            "sop": sop,
            "unexplained_amount": _m(r.unexplained_amount) if r else None,
            "unexplained_count": r.unexplained_count if r else None,
            "fees_net": _m(r.fees_net) if r else None,
            "tolerance_version": f"v{r.tolerance.version}" if r and r.tolerance_id else None,
            "ntsl_mapping_version": f"v{r.map_version.version}" if r and r.map_version_id else None,
            "settlement_case": None if case is None else {"id": case.pk, "status": case.status,
                                                          "link": f"/{slug}/cases/{case.pk}"},
            "links": {"settlement": f"/{slug}/dates/{d.isoformat()}/settlement" if r else None,
                      "date": f"/{slug}/dates/{d.isoformat()}"},
            "_result_id": r.pk if r else None,
        })
    used = [r for r in results.values()]
    ver = versions(rule_sets=[r.rule_run.rule_set.version_label for r in used if r.rule_run_id],
                   ntsl_mappings=[r.map_version.version for r in used if r.map_version_id],
                   tolerances=[r.tolerance.version for r in used if r.tolerance_id])
    return rows, ver


def settlement_totals(rows: list[dict]) -> dict:
    by_status = {s: 0 for s in SETTLEMENT_STATUSES}
    unexplained, fees = Decimal("0"), Decimal("0")
    for r in rows:
        by_status[r["status"] or NONE_STATUS] += 1
        unexplained += Decimal(r["unexplained_amount"] or 0)
        fees += Decimal(r["fees_net"] or 0)
    return {"by_status": by_status, "unexplained_amount": _m(unexplained), "fees_net": _m(fees)}


_SIDE_LABELS = dict(Side.choices)


def _settlement_sheets(rows: list[dict], totals: dict) -> list[Sheet]:
    cols = [Column("business_date", "Business date", DATE), Column("status", "Status"),
            Column("reasons", "Reasons"), Column("computed_at", "Computed at", DATETIME),
            Column("cycles_pass", "Self-check cycles PASS", COUNT),
            Column("cycles_total", "Self-check cycles", COUNT),
            Column("cbs_net", "SOP CBS net", AMOUNT), Column("ntsl_net", "SOP NTSL net", AMOUNT),
            Column("difference", "SOP difference", AMOUNT), Column("explained", "SOP explained", AMOUNT),
            Column("unexplained", "SOP unexplained", AMOUNT),
            Column("unexplained_amount", "Unexplained amount (cycles)", AMOUNT),
            Column("unexplained_count", "Unexplained count (cycles)", COUNT),
            Column("fees_net", "Fees net", AMOUNT), Column("tolerance_version", "Tolerance version"),
            Column("ntsl_mapping_version", "NTSL mapping version"),
            Column("case_id", "Settlement case", COUNT), Column("case_status", "Settlement case status")]

    def date_rows():
        for r in rows:
            case = r["settlement_case"]
            yield {"business_date": r["business_date"], "status": r["status_label"] or "No settlement result",
                   "reasons": " | ".join(r["reasons"]), "computed_at": r["computed_at"],
                   **r["self_check"], **(r["sop"] or {}), "unexplained_amount": r["unexplained_amount"],
                   "unexplained_count": r["unexplained_count"], "fees_net": r["fees_net"],
                   "tolerance_version": r["tolerance_version"], "ntsl_mapping_version": r["ntsl_mapping_version"],
                   "case_id": case["id"] if case else None, "case_status": case["status"] if case else None}
        yield {"business_date": "Total", "unexplained_amount": totals["unexplained_amount"],
               "fees_net": totals["fees_net"]}

    result_ids = [r["_result_id"] for r in rows if r["_result_id"]]
    cycle_qs = (SettlementCycleResult.objects.filter(result_id__in=result_ids)  # never the result's snapshot
                .order_by("result__business_date", "settlement_cycle__start_time", "settlement_cycle_id", "side"))
    cycle_cols = [Column("business_date", "Business date", DATE), Column("cycle", "Cycle"), Column("side", "Side"),
                  Column("ntsl_amount", "NTSL amount", AMOUNT), Column("ntsl_count", "NTSL count", COUNT),
                  Column("matched_amount", "Matched amount", AMOUNT),
                  Column("explained_amount", "Explained amount", AMOUNT),
                  Column("unexplained_amount", "Unexplained amount", AMOUNT),
                  Column("within_tolerance", "Within tolerance", BOOL)]

    def cycle_rows():
        for c in cycle_qs.values("result__business_date", "settlement_cycle__cycle_code", "side", "ntsl_amount",
                                 "ntsl_count", "matched_amount", "explained_amount", "unexplained_amount",
                                 "within_tolerance").iterator(chunk_size=2000):
            yield {"business_date": c["result__business_date"],
                   "cycle": c["settlement_cycle__cycle_code"] or "(no cycle)",
                   "side": _SIDE_LABELS.get(c["side"], c["side"]), "ntsl_amount": c["ntsl_amount"],
                   "ntsl_count": c["ntsl_count"], "matched_amount": c["matched_amount"],
                   "explained_amount": c["explained_amount"], "unexplained_amount": c["unexplained_amount"],
                   "within_tolerance": c["within_tolerance"]}

    return [Sheet("dates", "Dates", cols, date_rows, len(rows) + 1),
            Sheet("cycles", "Cycles", cycle_cols, cycle_rows, cycle_qs.count())]


class SettlementReportView(ReportView):
    def get(self, request, module):
        p, errors = request.query_params, {}
        d_from, d_to = report_range(p, errors)
        wanted = _enum_filter(p, "status", SETTLEMENT_STATUSES, errors)
        ordering = _ordering(p, errors)
        if errors:
            return _bad_request("Invalid input", errors)
        dates = _dates_in(self.module, d_from, d_to)
        if ordering == "business_date":
            dates.reverse()
        rows, ver = settlement_rows(self.module, dates)
        if wanted:
            rows = [r for r in rows if (r["status"] or NONE_STATUS) in wanted]
        totals = settlement_totals(rows)
        report = report_block("settlement", "Settlement report", self.module,
                              {"date_from": d_from, "date_to": d_to, "status": wanted or [], "ordering": ordering},
                              request.user, ver)
        sheets = _settlement_sheets(rows, totals)
        if wants_export(request):
            return export_response(request, report=report, module=self.module, sheets=sheets,
                                   file_base=range_file_base(self.module, "settlement", d_from, d_to))
        paginator = ReportDatePagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        response = paginator.get_paginated_response([{k: v for k, v in r.items() if not k.startswith("_")}
                                                     for r in page])
        response.data.update({"totals": totals, "report": report, "export": export_block(sheets)})
        return response


# ===========================================================================
# B3 transaction volume (DailyVolume, R4a)
# ===========================================================================
GROUP_BY = ("day", "month")
VOLUME_ORDERINGS = ("-period", "period")
_FILE_TYPE_ORDER = [RegisterType.CBS_GL_EXTRACT, RegisterType.RAW_DATA, RegisterType.MERCHANT_RAW_DATA,
                    RegisterType.MERCHANT_RAW_DATA_BBP, RegisterType.DRC, RegisterType.TIMEOUT,
                    RegisterType.ADJUSTMENT]
_SIDE_ORDER = list(VolumeSide.values)
_VOLUME_SIDE_LABELS = dict(VolumeSide.choices)


def _month_range(p, errors) -> tuple[date | None, date | None]:
    """group_by=month: the range widened to whole months, at most 24 months."""
    d_from, d_to = report_range(p, errors, max_days=None)
    if errors or d_from is None:
        return d_from, d_to
    d_from = d_from.replace(day=1)
    d_to = d_to.replace(day=calendar.monthrange(d_to.year, d_to.month)[1])
    months = (d_to.year - d_from.year) * 12 + d_to.month - d_from.month + 1
    if months > MAX_RANGE_MONTHS:
        errors["date_to"] = [f"At most {MAX_RANGE_MONTHS} months per report; narrow the dates."]
    return d_from, d_to


def _file_types(module) -> dict[str, str]:
    """{file type code: label} for every configured type (any version)."""
    out = {}
    for code, register_type in (FileTypeConfig.objects.filter(payment_module=module).order_by("file_type_code")
                                .values_list("file_type_code", "register_type")):
        out.setdefault(code, RegisterType(register_type).label.split(" -- ")[0].strip())
    return out


def _sides_block(acc: dict) -> list[dict]:
    return [{"side": s, "side_label": _VOLUME_SIDE_LABELS[s], "count": acc[s]["n"], "amount": _m(acc[s]["s"])}
            for s in _SIDE_ORDER if s in acc]


def _volume_entry(acc: dict, npci: dict | None) -> dict:
    return {"sides": _sides_block(acc), "total_count": sum(v["n"] for v in acc.values()),
            "total_amount": _m(sum((v["s"] for v in acc.values()), Decimal("0"))),
            "npci": npci}


def volume_rows(module, d_from: date, d_to: date, *, group_by: str, file_types: list[str] | None,
                by_cycle: bool) -> tuple[list[dict], list[dict]]:
    """(rows oldest period first, totals per file type)."""
    labels = _file_types(module)
    # 1C..10C then DC1 / DC2, as settlement.reconcile orders its cycles.
    codes = SettlementCycle.objects.filter(payment_module=module).values_list("cycle_code", flat=True)
    cycle_rank = {c: i for i, c in enumerate(sorted(codes, key=lambda c: (c.startswith("DC"), len(c), c)))}
    qs = DailyVolume.objects.filter(payment_module=module, business_date__gte=d_from, business_date__lte=d_to)
    if file_types:
        qs = qs.filter(file_type_code__in=file_types)
    groups: dict[tuple, dict] = {}
    totals: dict[str, dict] = {}

    def add(store, key, v):
        g = store.setdefault(key, {"acc": {}, "approved": None, "declined": None})
        a = g["acc"].setdefault(v.side, {"n": 0, "s": Decimal("0")})
        a["n"] += v.row_count
        a["s"] += v.amount
        if v.approved_count is not None:
            g["approved"] = (g["approved"] or 0) + v.approved_count
            g["declined"] = (g["declined"] or 0) + (v.declined_count or 0)

    for v in qs.order_by().iterator():
        period = v.business_date.isoformat() if group_by == "day" else v.business_date.strftime("%Y-%m")
        cycle = (v.cycle_code or None) if by_cycle else None
        add(groups, (period, v.file_type_code, cycle), v)
        add(totals, v.file_type_code, v)

    def type_rank(code):
        return (_FILE_TYPE_ORDER.index(code) if code in _FILE_TYPE_ORDER else len(_FILE_TYPE_ORDER), code)

    def npci(g):
        return None if g["approved"] is None else {"approved": g["approved"], "declined": g["declined"]}

    rows = []
    for (period, code, cycle) in sorted(groups, key=lambda k: (k[0], type_rank(k[1]),
                                                                 cycle_rank.get(k[2], 99) if k[2] else -1,
                                                                 k[2] or "")):
        g = groups[(period, code, cycle)]
        rows.append({"period": period, "file_type": code, "file_type_label": labels.get(code, code),
                     "cycle": cycle, **_volume_entry(g["acc"], npci(g))})
    total_rows = [{"file_type": code, "file_type_label": labels.get(code, code),
                   **_volume_entry(totals[code]["acc"], npci(totals[code]))}
                  for code in sorted(totals, key=type_rank)]
    return rows, total_rows


def volume_missing_dates(module, d_from: date, d_to: date) -> list[date]:
    """Dates in the range with files but no volume figures, or files changed
    since the figures were made (run again or `manage.py fill_daily_volume`)."""
    dates = _dates_in(module, d_from, d_to)
    done = dict(DailyVolumeDate.objects.filter(payment_module=module, business_date__in=dates)
                .values_list("business_date", "computed_at"))
    uploads = _latest_uploads(module, dates)
    return sorted(d for d in dates if d not in done or (uploads.get(d) and uploads[d] > done[d]))


def _volume_sheet(rows: list[dict], totals: list[dict]) -> Sheet:
    sides = [s for s in _SIDE_ORDER if any(x["side"] == s for r in rows for x in r["sides"])]
    cols = [Column("period", "Period"), Column("file_type_label", "File type"), Column("cycle", "Cycle")]
    for s in sides:
        cols += [Column(f"{s}_count", f"{_VOLUME_SIDE_LABELS[s]} count", COUNT),
                 Column(f"{s}_amount", f"{_VOLUME_SIDE_LABELS[s]} amount", AMOUNT)]
    cols += [Column("total_count", "Total count", COUNT), Column("total_amount", "Total amount", AMOUNT),
             Column("approved", "NPCI approved (00 / RB)", COUNT), Column("declined", "NPCI declined", COUNT)]

    def flat(r, period):
        out = {"period": period, "file_type_label": r["file_type_label"], "cycle": r.get("cycle"),
               "total_count": r["total_count"], "total_amount": r["total_amount"]}
        for x in r["sides"]:
            out[f"{x['side']}_count"], out[f"{x['side']}_amount"] = x["count"], x["amount"]
        if r["npci"]:
            out.update(approved=r["npci"]["approved"], declined=r["npci"]["declined"])
        return out

    def gen():
        for r in rows:
            yield flat(r, r["period"])
        for t in totals:
            yield flat(t, "Total")

    return Sheet("volume", "Volume", cols, gen, len(rows) + len(totals))


class VolumeReportView(ReportView):
    def get(self, request, module):
        p, errors = request.query_params, {}
        group_by = p.get("group_by") or "day"
        if group_by not in GROUP_BY:
            errors["group_by"] = [f"Allowed: {', '.join(GROUP_BY)}"]
            group_by = "day"
        d_from, d_to = _month_range(p, errors) if group_by == "month" else report_range(p, errors)
        file_types = _enum_filter(p, "file_type", list(_file_types(self.module)), errors)
        by_cycle = bool(_bool_param(p, "by_cycle", errors))
        ordering = p.get("ordering") or "-period"
        if ordering not in VOLUME_ORDERINGS:
            errors["ordering"] = [f"Allowed: {', '.join(VOLUME_ORDERINGS)}"]
        if errors:
            return _bad_request("Invalid input", errors)
        rows, totals = volume_rows(self.module, d_from, d_to, group_by=group_by, file_types=file_types,
                                   by_cycle=by_cycle)
        if ordering == "-period":  # newest period first; file types and cycles keep their order
            by_period: dict[str, list] = {}
            for r in rows:
                by_period.setdefault(r["period"], []).append(r)
            rows = [r for period in sorted(by_period, reverse=True) for r in by_period[period]]
        report = report_block("volume", "Transaction volume report", self.module,
                              {"date_from": d_from, "date_to": d_to, "group_by": group_by,
                               "file_type": file_types or [], "by_cycle": by_cycle, "ordering": ordering},
                              request.user, versions())
        sheets = [_volume_sheet(rows, totals)]
        if wants_export(request):
            return export_response(request, report=report, module=self.module, sheets=sheets,
                                   file_base=range_file_base(self.module, "volume", d_from, d_to))
        paginator = legacy.QueuePagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        response = paginator.get_paginated_response(page)
        response.data.update({"totals": totals, "missing_dates": volume_missing_dates(self.module, d_from, d_to),
                              "report": report, "export": export_block(sheets)})
        return response
