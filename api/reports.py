"""
/api/v1/<module>/reports/... (Phase 6, docs/phase6_api_contract.txt). The
first report built is the NPCI files report (UI refinements 8.1,
docs/ui_refinements_api_contract_u4u5.txt F1); report_range() and
report_block() are the contract's RR1 / RR2 and are meant for every later
report. Excel / CSV export (contract section A) is not built yet: "export"
is null until the shared export lands.

  GET /reports/npci-files/   every SSKUPI file (incl. _2, _3) by business date
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


def report_range(params, errors: dict) -> tuple[date | None, date | None]:
    """Phase 6 RR1: date_from / date_to inclusive; both omitted = this month
    (1st .. today, IST); one omitted, bad, reversed or > 92 days -> errors."""
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
    elif (d_to - d_from).days + 1 > MAX_RANGE_DAYS:
        errors["date_to"] = [f"At most {MAX_RANGE_DAYS} days per report; narrow the dates."]
    return d_from, d_to


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
