"""
/api/v1/<module>/ NPCI file generation (docs/npci_file_design_draft_v1.txt
section 5 + Amendment 1; contract for the frontend in
docs/npci_file_design_draft_v1.txt Amendment 1, A7 API contract). Thin views over npci_files.generator,
which checks the role, decides which cases go in and writes core.OperationLog.

  GET  /dates/<date>/npci-file/preview/   what the next file for the date would hold now (nothing saved)
  POST /dates/<date>/npci-file/           generate the next file (Checker)
  GET  /npci-files/                       generated files, newest first (paginated)
  GET  /npci-files/<id>/                  one file: counts, rows (masked), what was left out, history
  GET  /npci-files/<id>/download/         the stored bytes (Maker, Checker, Operations User; logged)

Account numbers: only the last 4 characters are ever returned as JSON; the
full values exist only in the downloaded file.
"""
from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response

from api.base import ModuleMixin, V1APIView
from api.operations import _DateMixin, _bad_request, _error, _history, _iso_date
from core.models import OperationLog, OperationType
from core.operations import OperationError
from exception_management import api as legacy
from exception_management.roles import user_roles
from npci_files import generator
from npci_files.formats import format_for, format_amount
from npci_files.models import NpciFile

V1_PREFIX = "/api/v1"


def _m(v) -> str:
    return f"{v:.2f}"


def _user_can(user, roles) -> bool:
    return bool(user_roles(user).intersection(roles))


def _download_url(module, f: NpciFile, user) -> str | None:
    if not generator.can_download(user):
        return None
    return f"{V1_PREFIX}/{module.code.lower()}/npci-files/{f.pk}/download/"


def downloads_by_file(module, file_ids) -> dict[int, list[dict]]:
    """{file id: [{"by", "at"}, ...] newest first} from core.OperationLog
    NPCI_FILE_DOWNLOADED, in ONE query for all the files of a list (owner
    2026-10-23: show who downloaded each file and when)."""
    out: dict[int, list[dict]] = {i: [] for i in file_ids}
    if not file_ids:
        return out
    logs = (OperationLog.objects.filter(payment_module=module, operation=OperationType.NPCI_FILE_DOWNLOADED,
                                        details__npci_file_id__in=list(file_ids))
            .order_by("-created_at", "-id").values_list("details", "username", "created_at"))
    for details, username, created_at in logs:
        out.setdefault(details.get("npci_file_id"), []).append({"by": username, "at": created_at})
    return out


def file_row(module, f: NpciFile, user, downloads: dict[int, list[dict]] | None = None) -> dict:
    """`downloads`: from downloads_by_file for a whole list; read here for a single file."""
    if downloads is None:
        downloads = downloads_by_file(module, [f.pk])
    history = downloads.get(f.pk, [])
    return {
        "downloads": history,
        "download_count": len(history),
        "id": f.pk, "file_date": f.file_date, "sequence": f.sequence, "file_name": f.file_name,
        "file_format": f.file_format, "row_count": f.row_count, "total_amount": _m(f.total_amount),
        "partial": f.partial, "not_included_count": len(f.not_included), "sha256": f.sha256,
        "size_bytes": f.size_bytes, "generated_by": f.generated_by_username, "generated_at": f.generated_at,
        "remarks": f.remarks, "download_url": _download_url(module, f, user),
    }


def _sent_files(module, file_date) -> list[dict]:
    return [{"id": f.pk, "file_name": f.file_name, "sequence": f.sequence, "row_count": f.row_count,
             "partial": f.partial, "generated_at": f.generated_at, "generated_by": f.generated_by_username}
            for f in NpciFile.objects.filter(payment_module=module, file_date=file_date).order_by("sequence")]


def preview_payload(module, plan: generator.FilePlan, user) -> dict:
    sequence = plan.next_sequence()
    already = sorted({row.npci_file.file_name for _case, row in plan.already_sent})
    return {
        "file_date": plan.file_date,
        "file_format": plan.file_format.code,
        "next_sequence": sequence,
        "next_file_name": plan.file_format.file_name(plan.file_date, sequence),
        "row_count": len(plan.rows),
        "total_amount": _m(plan.total_amount),
        "partial": plan.partial,
        "counts": [{**c, "amount": _m(c["amount"])} for c in plan.counts()],
        "rows": [{"line": i, "case_id": r.case.pk, "case_business_date": r.case.business_date, "rrn": r.line.rrn,
                  "rule_code": r.case.rule_code, "action_type": r.action_type, "flag": r.flag, "code": r.code,
                  "shtdat": r.line.shtdat, "amount": format_amount(r.line.amount),
                  "account_masked": generator.mask_account(r.line.account), "text": r.line.text}
                 for i, r in enumerate(plan.rows, start=1)],
        "not_included": [generator.excluded_payload(e) for e in plan.excluded],
        "not_included_counts": plan.excluded_counts(),
        "already_sent_count": len(plan.already_sent),
        "already_sent_files": already,
        "warnings": plan.warnings,
        "files": _sent_files(module, plan.file_date),
        "can_generate": bool(plan.rows) and _user_can(user, generator.GENERATE_ROLES),
    }


class NpciView(_DateMixin, ModuleMixin, V1APIView):
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.file_format = format_for(self.module)

    def no_format(self) -> Response | None:
        if self.file_format is None:
            return Response({"detail": f"No NPCI file format is defined for {self.module.code}."},
                            status=status.HTTP_404_NOT_FOUND)
        return None


class NpciPreviewView(NpciView):
    def get(self, request, module, business_date):
        d, error = self.business_date(business_date)
        if error or (error := self.no_format()):
            return error
        return Response(preview_payload(self.module, generator.plan_file(self.module, d), request.user))


class NpciGenerateView(NpciView):
    """POST {"confirm_partial": bool, "remarks": "..."}."""

    def post(self, request, module, business_date):
        d, error = self.business_date(business_date)
        if error or (error := self.no_format()):
            return error
        data = request.data if isinstance(request.data, dict) else {}
        confirm = data.get("confirm_partial", False)
        remarks = data.get("remarks", "")
        errors = {}
        if not isinstance(confirm, bool):
            errors["confirm_partial"] = ["true or false."]
        if not isinstance(remarks, str):
            errors["remarks"] = ["Text."]
        if errors:
            return _bad_request("Invalid input", errors)
        try:
            f = generator.generate_file(request.user, self.module, d, confirm_partial=confirm, remarks=remarks,
                                        source="api")
        except OperationError as exc:
            return _error(exc)
        return Response(_file_detail(self.module, f, request.user), status=status.HTTP_201_CREATED)


class NpciFileListView(NpciView):
    """?file_date=YYYY-MM-DD  ?date_from=  ?date_to=; newest first."""

    def get(self, request, module):
        p, errors = request.query_params, {}
        qs = NpciFile.objects.filter(payment_module=self.module)
        for name, lookup in (("file_date", "file_date"), ("date_from", "file_date__gte"), ("date_to", "file_date__lte")):
            if p.get(name):
                d = _iso_date(p[name])
                if d is None:
                    errors[name] = ["Enter a date as YYYY-MM-DD."]
                else:
                    qs = qs.filter(**{lookup: d})
        if errors:
            return _bad_request("Invalid input", errors)
        paginator = legacy.QueuePagination()
        page = paginator.paginate_queryset(qs.order_by("-generated_at", "-pk"), request, view=self)
        downloads = downloads_by_file(self.module, [f.pk for f in page])
        response = paginator.get_paginated_response([file_row(self.module, f, request.user, downloads)
                                                     for f in page])
        response.data["can_download"] = generator.can_download(request.user)
        return response


def _file_detail(module, f: NpciFile, user) -> dict:
    body = file_row(module, f, user)
    body["counts"] = f.counts
    body["warnings"] = f.warnings
    body["not_included"] = f.not_included
    body["rows"] = [{"line": r.line, "case_id": r.case_id, "case_business_date": r.case_business_date, "rrn": r.rrn,
                     "flag": r.flag, "code": r.code, "shtdat": r.shtdat, "amount": format_amount(r.amount),
                     "account_masked": r.account_masked, "text": r.text}
                    for r in f.rows.order_by("line")]
    body["other_files_for_date"] = [x for x in _sent_files(module, f.file_date) if x["id"] != f.pk]
    body["history"] = _history(OperationLog.objects.filter(payment_module=module, details__npci_file_id=f.pk)
                               .order_by("created_at", "id"))
    return body


class NpciFileDetailView(NpciView):
    def get(self, request, module, pk):
        f = get_object_or_404(NpciFile.objects.filter(payment_module=self.module), pk=pk)
        return Response(_file_detail(self.module, f, request.user))


class NpciFileDownloadView(NpciView):
    def get(self, request, module, pk):
        f = get_object_or_404(NpciFile.objects.filter(payment_module=self.module), pk=pk)
        try:
            data = generator.download_file(request.user, f, source="api")
        except OperationError as exc:
            return _error(exc)
        response = HttpResponse(data, content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{f.file_name}"'
        response["Content-Length"] = str(len(data))
        response["X-Content-SHA256"] = f.sha256
        response["Cache-Control"] = "no-store"
        return response


def case_npci_file(case) -> dict | None:
    """For the case page: "included in file SSKUPI14072026.csv" (design section 6)."""
    row = case.npci_file_rows.select_related("npci_file").first()
    if row is None:
        return None
    f = row.npci_file
    return {"id": f.pk, "file_name": f.file_name, "file_date": f.file_date, "line": row.line,
            "generated_at": f.generated_at}
