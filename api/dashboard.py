"""
/api/v1/<module>/dashboard/ (UI refinements 8.3; docs/ui_refinements_api_contract_u4u5.txt
F2). Read-only, all five roles. Plain numbers per business date, oldest
first, for charts drawn by the frontend (no chart-package-specific shape):
volume, exceptions by reply type, settlement, SSKUPI state, plus the module's
open cases by ageing bucket now.

A fixed number of queries whatever the number of dates: every block is one
grouped query over all the dates, and the date status uses the cheap
FILE_PENDING rule (npci_files.replies.finish_date_statuses exact=False).
"""
from __future__ import annotations

from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.response import Response

from api.base import ModuleMixin, V1APIView, module_slug
from api.operations import _bad_request
from api.reports import report_range
from exception_management.models import ACTIVE_STATUSES, CaseStatus, ExceptionCase
from npci_files import replies as npci_replies
from reconciliation.overview import business_dates, date_overviews
from rule_engine.models import AgeingBucket
from settlement.models import ResultStatus
from settlement.reconcile import CBS_REGISTER_TYPE, NPCI_REGISTER_TYPES
from transactions.models import TransactionFact

DEFAULT_DATES = 30


def _m(v) -> str:
    return f"{v or 0:.2f}"


def _volumes(module, dates) -> dict:
    """{date: {"npci": {count, amount}, "cbs": {count, amount}}}: one grouped query."""
    out = {d: {"npci": {"count": 0, "amount": 0}, "cbs": {"count": 0, "amount": 0}} for d in dates}
    rows = (TransactionFact.objects.filter(payment_module=module, business_date__in=dates,
                                           source_register_type__in=[*NPCI_REGISTER_TYPES, CBS_REGISTER_TYPE],
                                           uploaded_file__is_current=True)
            .order_by().values("business_date", "source_register_type").annotate(n=Count("id"), s=Sum("amount")))
    for r in rows:
        side = "cbs" if r["source_register_type"] == CBS_REGISTER_TYPE else "npci"
        v = out[r["business_date"]][side]
        v["count"] += r["n"]
        v["amount"] += r["s"] or 0
    return {d: {k: {"count": v["count"], "amount": _m(v["amount"])} for k, v in sides.items()}
            for d, sides in out.items()}


def _exceptions(module, dates) -> dict:
    types = npci_replies.ReplyType.values
    qs = (npci_replies.annotate_reply_type(
        ExceptionCase.objects.filter(payment_module=module, business_date__in=dates)
        .exclude(status=CaseStatus.CLOSED_SUPERSEDED))
        .order_by().values("business_date").annotate(**{t: Count("id", filter=Q(reply_type=t)) for t in types}))
    out = {d: {**{t: 0 for t in types}, "total": 0} for d in dates}
    for r in qs:
        out[r["business_date"]] = {**{t: r[t] for t in types}, "total": sum(r[t] for t in types)}
    return out


def open_cases_by_age(module) -> dict:
    """Every active case of the module now, by ageing bucket (the queue's own
    annotation); cases with no bucket (settlement cases) are in the total only."""
    rows = {r["ageing_bucket"]: r for r in (
        ExceptionCase.objects.filter(payment_module=module, status__in=ACTIVE_STATUSES).with_ageing()
        .order_by().values("ageing_bucket").annotate(n=Count("id"), overdue=Count("id", filter=Q(is_overdue=True))))}
    buckets = [{"label": b.label, "min_days": b.min_days, "max_days": b.max_days,
                "count": rows.get(b.label, {}).get("n", 0), "overdue": rows.get(b.label, {}).get("overdue", 0)}
               for b in AgeingBucket.objects.filter(payment_module=module).order_by("order")]
    return {"buckets": buckets, "total": sum(r["n"] for r in rows.values()),
            "overdue": sum(r["overdue"] for r in rows.values())}


class DashboardView(ModuleMixin, V1APIView):
    def get(self, request, module):
        p, errors = request.query_params, {}
        all_dates = business_dates(self.module)  # newest first
        if p.get("date_from") or p.get("date_to"):
            d_from, d_to = report_range(p, errors)
            if errors:
                return _bad_request("Invalid input", errors)
            dates = [d for d in all_dates if d_from <= d <= d_to]
        else:
            dates = all_dates[:DEFAULT_DATES]
            d_from, d_to = (dates[-1], dates[0]) if dates else (None, None)
        dates = sorted(dates)  # oldest first, for charts

        overviews = date_overviews(self.module, dates)
        files = npci_replies.date_file_summaries(self.module, dates)
        statuses = {d: {k: overviews[d][k] for k in ("status", "status_label", "status_detail", "last_run")}
                    for d in dates}
        npci_replies.finish_date_statuses(self.module, statuses, files, exact=False)
        volumes, exceptions = _volumes(self.module, dates), _exceptions(self.module, dates)
        slug = module_slug(self.module)
        rows = []
        for d in dates:
            ov, st, sk = overviews[d], statuses[d], files[d]
            settlement = ov["settlement"]
            rows.append({
                "business_date": d, "status": st["status"], "status_label": st["status_label"],
                "volume": {"transactions": ov["matrix"]["match_groups"], **volumes[d]},
                "exceptions": exceptions[d],
                "open_cases": ov["cases"]["active"],
                "settlement": None if settlement is None else {
                    "status": settlement["status"], "tallied": settlement["status"] == ResultStatus.TALLIED,
                    "unexplained_amount": settlement["unexplained_amount"]},
                "sskupi": None if sk is None else {
                    "state": sk["state"], "file_count": sk["file_count"], "not_downloaded": len(sk["not_downloaded"]),
                    "pending": st["status"] == "FILE_PENDING"},
                "link": f"/{slug}/dates/{d.isoformat()}",
            })
        return Response({
            "range": {"date_from": d_from, "date_to": d_to, "dates": len(rows)},
            "reply_types": [{"value": v, "label": label} for v, label in npci_replies.ReplyType.choices],
            "dates": rows,
            "open_cases_by_age": open_cases_by_age(self.module),
            "generated_at": timezone.now(),
        })

