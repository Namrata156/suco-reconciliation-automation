"""
Phase 6 reports, common pieces (docs/phase6_api_contract.txt): report_range()
(RR1), report_block() / versions() (RR2), the B1 / B2 paging, the B0 meta,
and the NPCI files report (UI refinements 8.1,
docs/ui_refinements_api_contract_u4u5.txt F1; its "export" stays null, the
download is not part of Phase 6).

  GET /api/v1/reports/meta/                   B0
  GET /api/v1/<module>/reports/npci-files/    every SSKUPI file (incl. _2, _3) by business date

The other reports: api.reports_daily (B1-B3), api.reports_cases (B4-B6s),
api.reports_users (B7); B8 in api.results; downloads in api.export.
"""
from __future__ import annotations

from datetime import date

from django.db.models import BigIntegerField, Count, Exists, OuterRef, Q, Sum
from django.db.models.fields.json import KT
from django.db.models.functions import Cast
from django.utils import timezone
from rest_framework.response import Response

from api.base import ModuleMixin, V1APIView, module_slug
from api.npci_files import downloads_by_file, file_row
from api.operations import _bad_request, _iso_date
from api.results import _bool_param
from core.models import OperationLog, OperationType
from exception_management import api as legacy
from npci_files.models import NpciFile

MAX_RANGE_DAYS = 92
MAX_RANGE_MONTHS = 24


class ReportDatePagination(legacy.QueuePagination):
    """B1 / B2: one row per business date; default 50, at most 92 (owner D5)."""

    max_page_size = MAX_RANGE_DAYS


def report_range(params, errors: dict, *, max_days: int | None = MAX_RANGE_DAYS) -> tuple[date | None, date | None]:
    """Phase 6 RR1: date_from / date_to inclusive; both omitted = this month
    (1st .. today, IST); one omitted, bad, reversed or > 92 days -> errors.
    max_days None: no day limit (the caller checks its own)."""
    raw_from, raw_to = params.get("date_from"), params.get("date_to")
    if not raw_from and not raw_to:
        today = timezone.localdate()
        return today.replace(day=1), today
    if not raw_from or not raw_to:
        errors["date_to" if raw_from else "date_from"] = ["Give both dates."]
        return None, None
    d_from, d_to = _iso_date(raw_from), _iso_date(raw_to)
    for name, value in (("date_from", d_from), ("date_to", d_to)):
        if value is None:
            errors[name] = ["Enter a date as YYYY-MM-DD."]
    if d_from is None or d_to is None:
        return None, None
    if d_to < d_from:
        errors["date_to"] = ["Must not be before date_from."]
    elif max_days is not None and (d_to - d_from).days + 1 > max_days:
        errors["date_to"] = [f"At most {max_days} days per report; narrow the dates."]
    return d_from, d_to


def version_list(values) -> list[str]:
    """Distinct version labels, "v1" style, sorted naturally."""
    out = set()
    for v in values:
        if v in (None, ""):
            continue
        out.add(v if isinstance(v, str) else f"v{v}")
    return sorted(out, key=lambda s: [int(x) if x.isdigit() else x for x in s.lstrip("v").split(".")])


def versions(rule_sets=(), ntsl_mappings=(), tolerances=()) -> dict:
    return {"rule_sets": version_list(rule_sets), "ntsl_mappings": version_list(ntsl_mappings),
            "tolerances": version_list(tolerances)}


def report_block(key: str, title: str, module, filters: dict, user, versions: dict | None = None) -> dict:
    """Phase 6 RR2."""
    return {"key": key, "title": title, "module": module_slug(module) if module else None, "filters": filters,
            "generated_at": timezone.now(), "generated_by": user.get_username(),
            "versions": versions or {"rule_sets": [], "ntsl_mappings": [], "tolerances": []}}


class ReportView(ModuleMixin, V1APIView):
    pass


class NpciFilesReportView(ReportView):
    ORDERINGS = {"-business_date": ("-file_date", "sequence", "pk"), "business_date": ("file_date", "sequence", "pk")}

    def get(self, request, module):
        p, errors = request.query_params, {}
        d_from, d_to = report_range(p, errors)
        downloaded = _bool_param(p, "downloaded", errors)
        ordering = p.get("ordering") or "-business_date"
        if ordering not in self.ORDERINGS:
            errors["ordering"] = [f"Allowed: {', '.join(self.ORDERINGS)}"]
        if errors:
            return _bad_request("Invalid input", errors)
        was_downloaded = Exists(OperationLog.objects.filter(
            payment_module=self.module, operation=OperationType.NPCI_FILE_DOWNLOADED)
            .annotate(file_id=Cast(KT("details__npci_file_id"), BigIntegerField())).filter(file_id=OuterRef("pk")))
        qs = (NpciFile.objects.filter(payment_module=self.module, file_date__gte=d_from, file_date__lte=d_to)
              .annotate(was_downloaded=was_downloaded))
        if downloaded is not None:
            qs = qs.filter(was_downloaded=downloaded)
        totals = qs.order_by().aggregate(files=Count("id"), rows=Sum("row_count"), amount=Sum("total_amount"),
                                         downloaded=Count("id", filter=Q(was_downloaded=True)))
        paginator = legacy.QueuePagination()
        page = paginator.paginate_queryset(qs.defer("content").order_by(*self.ORDERINGS[ordering]), request,
                                           view=self)
        downloads = downloads_by_file(self.module, [f.pk for f in page])
        slug = module_slug(self.module)
        rows = []
        for f in page:
            row = file_row(self.module, f, request.user, downloads)
            row.update({"business_date": f.file_date,
                        "links": {"file": f"/{slug}/npci-files/{f.pk}", "date": f"/{slug}/dates/{f.file_date}"}})
            rows.append(row)
        response = paginator.get_paginated_response(rows)
        response.data["totals"] = {
            "files": totals["files"], "rows": totals["rows"] or 0, "amount": f"{totals['amount'] or 0:.2f}",
            "downloaded": totals["downloaded"], "not_downloaded": totals["files"] - totals["downloaded"]}
        response.data["report"] = report_block(
            "npci_files", "NPCI files (SSKUPI)", self.module,
            {"date_from": d_from, "date_to": d_to, "downloaded": downloaded, "ordering": ordering}, request.user)
        response.data["export"] = None  # the shared Phase 6 export is not built yet
        return response


# ---------------------------------------------------------------------------
# B0 /api/v1/reports/meta/ (static facts for the screens; no module)
# ---------------------------------------------------------------------------
REPORT_CATALOGUE = [
    # key, title, description, module_scoped, capability (menu order, frontend lib/reports.ts)
    ("reconciliation", "Daily reconciliation report",
     "How each business date reconciled: files, transactions, cases and settlement status, one row per date.",
     True, "reports.view"),
    ("settlement", "Settlement report",
     "Whether each date's NPCI settlement tied to CBS, and the unexplained amount if not.", True, "reports.view"),
    ("npci_files", "NPCI files (SSKUPI)",
     "Every SSKUPI file generated, with rows and amount, who generated it and who downloaded it, and when.",
     True, "reports.view"),
    ("volume", "Daily / monthly transaction volume",
     "Transactions and amounts each source file carried, per day or per month.", True, "reports.view"),
    ("exceptions", "Exception report", "Exception cases for a period, open or closed, with the action taken.",
     True, "reports.view"),
    ("ageing", "Ageing report", "Open cases by age bucket and rule, with the overdue counts.", True,
     "reports.view"),
    ("approvals", "Approval report",
     "Every maker-checker decision in a period: who proposed, who decided, self-approvals.", True,
     "reports.approvals"),
    ("user_activity", "User activity report", "Logins and work done per user in a period.", False,
     "reports.user_activity"),
]


class ReportsMetaView(V1APIView):
    def get(self, request):
        from api.export import EXPORT_LIMIT, export_formats

        return Response({
            "export_formats": export_formats(),
            "max_export_rows": EXPORT_LIMIT,
            "max_range_days": MAX_RANGE_DAYS,
            "max_range_months": MAX_RANGE_MONTHS,
            "reports": [{"key": k, "title": t, "description": d, "module_scoped": s, "capability": c}
                        for k, t, d, s, c in REPORT_CATALOGUE],
        })
