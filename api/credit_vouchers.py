"""
/api/v1/<module>/dates/<date>/credit-voucher/... the CBS credit file ("rev"
file, owner 2026-11-11). Thin views over npci_files.credit_voucher, which
checks the role, decides which cases go in, approves on upload and writes
core.OperationLog. Same style as api/npci_files.py.

  GET  /dates/<d>/credit-voucher/preview/          what the next file would hold now (nothing saved); any role
  GET  /dates/<d>/credit-voucher/                  the date's versions, newest first; any role
  POST /dates/<d>/credit-voucher/                  generate the next version (Maker, Operations User)
  GET  /dates/<d>/credit-voucher/<id>/             one version: rows (accounts masked), approval, history
  GET  /dates/<d>/credit-voucher/<id>/download/    the stored CSV (Maker, Operations User, Checker; logged)
  POST /dates/<d>/credit-voucher/<id>/upload/      multipart "file" (+ "remarks"): the checked file; a
                                                   Checker other than the generating user. 200 = approved;
                                                   (owner 2026-11-12: the roles and the other-user rule apply
                                                   only with ROLES_ENFORCED; otherwise anyone signed in)
                                                   400 = refused, errors per row; 403; 409

Account numbers: only the last 4 characters are ever returned as JSON; the
full values exist only in the downloaded file.
"""
from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from api.base import ModuleMixin, V1APIView
from api.operations import _DateMixin, _bad_request, _error, _history
from core.models import OperationLog, OperationType
from core.operations import OperationError
from exception_management.roles import roles_enforced
from npci_files import credit_voucher as cv
from npci_files import generator
from npci_files.models import CreditVoucher, CreditVoucherApproval, CreditVoucherCredit, CreditVoucherStatus

V1_PREFIX = "/api/v1"


def _past(d) -> bool:
    """False for today and later: generation refuses them (require_past_business_date)."""
    return d < timezone.localdate()


def _m(v) -> str:
    return f"{v:.2f}"


def _url(module, v: CreditVoucher, what: str = "") -> str:
    return f"{V1_PREFIX}/{module.code.lower()}/dates/{v.voucher_date.isoformat()}/credit-voucher/{v.pk}/{what}"


def _downloads(module, voucher_ids) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {i: [] for i in voucher_ids}
    if not voucher_ids:
        return out
    for details, username, created_at in (
            OperationLog.objects.filter(payment_module=module, operation=OperationType.CREDIT_VOUCHER_DOWNLOADED,
                                        details__credit_voucher_id__in=list(voucher_ids))
            .order_by("-created_at", "-id").values_list("details", "username", "created_at")):
        out.setdefault(details.get("credit_voucher_id"), []).append({"by": username, "at": created_at})
    return out


def _approval(a: CreditVoucherApproval | None) -> dict | None:
    if a is None:
        return None
    return {"approved_by": a.approved_by_username, "approved_at": a.approved_at,
            "uploaded_file_name": a.uploaded_file_name, "uploaded_sha256": a.uploaded_sha256,
            "approved_count": a.approved_count, "left_open_count": a.removed_count,
            "total_amount": _m(a.total_amount)}


def voucher_row(module, v: CreditVoucher, user, downloads: dict | None = None) -> dict:
    if downloads is None:
        downloads = _downloads(module, [v.pk])
    history = downloads.get(v.pk, [])
    st = cv.voucher_status(v)
    approval = CreditVoucherApproval.objects.filter(voucher=v).first()
    return {
        "id": v.pk, "voucher_date": v.voucher_date, "version": v.version, "file_name": v.file_name,
        "status": str(st), "status_label": CreditVoucherStatus(st).label,
        "row_count": v.row_count, "total_amount": _m(v.total_amount), "rows_to_check": v.rows_to_check,
        "counts": v.counts, "not_included_count": len(v.not_included), "debit_account_masked":
            generator.mask_account(v.debit_account), "narration": v.narration, "sha256": v.sha256,
        "size_bytes": v.size_bytes, "generated_by": v.generated_by_username, "generated_at": v.generated_at,
        "remarks": v.remarks, "downloads": history, "download_count": len(history),
        "download_url": _url(module, v, "download/") if cv.can(user, cv.DOWNLOAD_ROLES) else None,
        "upload_url": _url(module, v, "upload/") if st == CreditVoucherStatus.GENERATED else None,
        "can_upload": (st == CreditVoucherStatus.GENERATED and cv.can(user, cv.UPLOAD_ROLES)
                       and not (roles_enforced() and v.generated_by_id == getattr(user, "pk", None))),
        "approval": _approval(approval),
    }


def voucher_detail(module, v: CreditVoucher, user) -> dict:
    body = voucher_row(module, v, user)
    credits = {c.voucher_row_id: c for c in CreditVoucherCredit.objects.filter(voucher_row__voucher=v)}
    body["rows"] = [{
        "line": r.line, "sr_no": r.line + 1, "case_id": r.case_id, "rule_code": r.rule_code,
        "action_type": r.action_type, "rrn": r.rrn, "txn_date": r.txn_date, "amount": _m(r.amount),
        "account_masked": generator.mask_account(r.account) if r.account else "", "check": r.check_note,
        "narration": cv.credit_narration(r.rrn, r.txn_date),
        "approved": r.pk in credits,
        "uploaded_account_masked": generator.mask_account(credits[r.pk].account) if r.pk in credits else None,
        "account_changed": credits[r.pk].account_changed if r.pk in credits else None,
    } for r in v.rows.order_by("line")]
    body["not_included"] = v.not_included
    body["warnings"] = v.warnings
    body["history"] = _history(OperationLog.objects.filter(payment_module=module, details__credit_voucher_id=v.pk)
                               .order_by("created_at", "id"))
    return body


def preview_payload(module, plan: cv.VoucherPlan, user) -> dict:
    version = plan.next_version()
    return {
        "voucher_date": plan.voucher_date,
        "next_version": version,
        "next_file_name": cv.file_name(plan.voucher_date, version),
        "debit_account_masked": generator.mask_account(plan.debit_account),
        "narration": plan.narration,
        "row_count": len(plan.rows),
        "total_amount": _m(plan.total_amount),
        "rows_to_check": plan.rows_to_check,
        "counts": plan.counts(),
        "rows": [{"line": i, "sr_no": i + 1, "case_id": r.case.pk, "case_business_date": r.case.business_date,
                  "case_status": r.case.status, "rule_code": r.case.rule_code, "action_type": r.action_type,
                  "rrn": r.rrn, "txn_date": r.txn_date, "amount": _m(r.amount),
                  "account_masked": generator.mask_account(r.account) if r.account else "",
                  "check": r.check_note, "narration": r.narration}
                 for i, r in enumerate(plan.rows, start=1)],
        "not_included": [e.payload() for e in plan.excluded],
        "not_included_counts": plan.excluded_counts(),
        "warnings": plan.warnings(),
        "can_generate": bool(plan.rows) and _past(plan.voucher_date) and cv.can(user, cv.GENERATE_ROLES),
    }


class _VoucherView(_DateMixin, ModuleMixin, V1APIView):
    def voucher(self, d, pk) -> CreditVoucher:
        return get_object_or_404(CreditVoucher.objects.filter(payment_module=self.module, voucher_date=d), pk=pk)


class CreditVoucherPreviewView(_VoucherView):
    def get(self, request, module, business_date):
        d, error = self.business_date(business_date)
        if error:
            return error
        return Response(preview_payload(self.module, cv.plan_voucher(self.module, d), request.user))


class CreditVoucherListView(_VoucherView):
    """GET: the date's versions, newest first. POST {"remarks": "..."}: generate."""

    def get(self, request, module, business_date):
        d, error = self.business_date(business_date)
        if error:
            return error
        vouchers = list(CreditVoucher.objects.filter(payment_module=self.module, voucher_date=d)
                        .order_by("-version"))
        downloads = _downloads(self.module, [v.pk for v in vouchers])
        rows = [voucher_row(self.module, v, request.user, downloads) for v in vouchers]
        return Response({"voucher_date": d, "results": rows, "latest": rows[0] if rows else None,
                         "pending_approval": bool(rows) and rows[0]["status"] == CreditVoucherStatus.GENERATED,
                         "can_generate": _past(d) and cv.can(request.user, cv.GENERATE_ROLES),
                         "can_download": cv.can(request.user, cv.DOWNLOAD_ROLES)})

    def post(self, request, module, business_date):
        d, error = self.business_date(business_date)
        if error:
            return error
        data = request.data if isinstance(request.data, dict) else {}
        remarks = data.get("remarks", "")
        if not isinstance(remarks, str):
            return _bad_request("Invalid input", {"remarks": ["Text."]})
        try:
            v = cv.generate_voucher(request.user, self.module, d, remarks=remarks, source="api")
        except OperationError as exc:
            return _error(exc)
        return Response(voucher_detail(self.module, v, request.user), status=status.HTTP_201_CREATED)


class CreditVoucherDetailView(_VoucherView):
    def get(self, request, module, business_date, pk):
        d, error = self.business_date(business_date)
        if error:
            return error
        return Response(voucher_detail(self.module, self.voucher(d, pk), request.user))


class CreditVoucherDownloadView(_VoucherView):
    def get(self, request, module, business_date, pk):
        d, error = self.business_date(business_date)
        if error:
            return error
        v = self.voucher(d, pk)
        try:
            data = cv.download_voucher(request.user, v, source="api")
        except OperationError as exc:
            return _error(exc)
        response = HttpResponse(data, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{v.file_name}"'
        response["Content-Length"] = str(len(data))
        response["X-Content-SHA256"] = v.sha256
        response["Cache-Control"] = "no-store"
        return response


class CreditVoucherUploadView(_VoucherView):
    """POST multipart: "file" = the checked CSV, "remarks" optional."""

    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, module, business_date, pk):
        d, error = self.business_date(business_date)
        if error:
            return error
        v = self.voucher(d, pk)
        upload = request.FILES.get("file")
        if upload is None:
            return _bad_request("Invalid input", {"file": ["Attach the checked credit file as 'file'."]})
        if upload.size > cv.MAX_UPLOAD_BYTES:
            return _bad_request("Invalid input", {"file": ["The file is too large."]})
        remarks = request.data.get("remarks", "") or ""
        try:
            result = cv.approve_upload(request.user, v, upload.name, upload.read(), remarks=str(remarks),
                                       source="api")
        except OperationError as exc:
            return _error(exc)
        return Response({
            "approved_count": len(result.approved),
            "left_open_count": len(result.left_open),
            "approved_case_ids": [r.case_id for r in result.approved],
            "left_open": [{"case_id": r.case_id, "rrn": r.rrn, "rule_code": r.rule_code, "amount": _m(r.amount)}
                          for r in result.left_open],
            "accounts_changed": result.approval.credits.filter(account_changed=True).count(),
            "voucher": voucher_detail(self.module, v, request.user),
        })
