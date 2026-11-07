"""
/api/v1/<module>/ files, CBS date selection, business dates and "Run"
(Phase 5 Slice 2; design docs/phase5_design_draft_v1.txt 4.8-4.11, P5, P6,
P10b, Amendment 1). Thin views over file_processing.intake and
reconciliation.runs / overview, which check the role (Operations User for
every write), the status, and write core.OperationLog.

  POST /files/detect/              dry run: proposed type / cycle / date per file
  POST /files/                     upload (multipart); each file accepted or refused
  GET  /files/                     list (filters: business_date, file_type, file_type_config,
                                   cycle, status, current; ordering)
  GET  /files/<id>/                detail (+ date selection, operations history)
  GET  /files/<id>/exceptions/     the file's validation exceptions (paginated)
  POST /files/<id>/select-date/    CBS business date
  GET  /dates/                     business-date overview (paginated)
  GET  /dates/<date>/              one date: checklist, runs, operations history
  POST /dates/<date>/run/          queue the run chain (202)

UI refinements U2 / U3 (docs/ui_refinements_api_contract.txt A2, B2):
  GET  /files/<id>/original/       the stored upload, byte for byte (all five roles; logged)
  dates list / detail: plain "status" and a compact SSKUPI file block per date
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from django.db.models import Count, Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from api.base import ModuleMixin, V1APIView
from core.models import OperationLog, OperationType
from core.operations import OperationError, RoleRequired, WrongState, log_operation
from exception_management import api as legacy
from file_processing import intake
from file_processing.models import FileValidationException, ProcessingStatus, UploadedFile, ValidationSeverity
from npci_files.replies import date_file_summaries, finish_date_statuses
from reconciliation.models import ReconciliationRun
from reconciliation.overview import business_dates, date_overviews
from reconciliation.runs import run_payload, start_run


def _bad_request(detail: str, errors: dict | None = None) -> Response:
    body = {"detail": detail}
    if errors:
        body["errors"] = errors
    return Response(body, status=status.HTTP_400_BAD_REQUEST)


def _error(exc: OperationError) -> Response:
    code = (status.HTTP_403_FORBIDDEN if isinstance(exc, RoleRequired)
            else status.HTTP_409_CONFLICT if isinstance(exc, WrongState) else status.HTTP_400_BAD_REQUEST)
    body = {"detail": exc.detail}
    if exc.errors:
        body["errors"] = exc.errors
    return Response(body, status=code)


def _iso_date(value) -> date | None:
    try:
        return parse_date(str(value or "").strip())
    except ValueError:
        return None


class OpsView(ModuleMixin, V1APIView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]


# ---------------------------------------------------------------------------
# serialisation
# ---------------------------------------------------------------------------
def _history(qs) -> list[dict]:
    return [{"id": e.pk, "operation": e.operation, "operation_label": e.get_operation_display(),
             "username": e.username, "created_at": e.created_at, "source": e.source,
             "business_date": e.business_date, "uploaded_file_id": e.uploaded_file_id, "run_id": e.run_id,
             "details": e.details} for e in qs]


def file_row(f: UploadedFile) -> dict:
    c = f.file_type_config
    counts = {sev: getattr(f, f"n_{sev.lower()}", None) for sev in ValidationSeverity.values}
    return {
        "id": f.pk,
        "filename": f.filename,
        "file_type": {"id": c.pk, "file_type_code": c.file_type_code, "version": c.version,
                      "register_type": c.register_type, "label": intake.config_label(c)},
        "cycle": f.settlement_cycle.cycle_code if f.settlement_cycle else None,
        "business_date": f.business_date,
        "batch_business_date": f.batch_business_date,
        "processing_status": f.processing_status,
        "processing_status_label": f.get_processing_status_display(),
        "state": intake._file_state(f.processing_status),
        "parser_status": f.parser_status,
        "detected_format": f.detected_format,
        "size_bytes": f.size_bytes,
        "sha256": f.sha256_hash,
        "source_system": f.source_system,
        "uploaded_by": f.uploaded_by.get_username() if f.uploaded_by else None,
        "uploaded_at": f.uploaded_at,
        "valid_record_count": f.valid_record_count,
        "rejected_record_count": f.rejected_record_count,
        "is_current": f.is_current,
        "is_reprocess": f.is_reprocess,
        "exception_counts": counts if None not in counts.values() else None,
        "date_selection": intake.date_selection(f),
    }


def _files(module):
    return (UploadedFile.objects.filter(payment_module=module)
            .select_related("file_type_config", "settlement_cycle", "uploaded_by")
            .annotate(**{f"n_{sev.lower()}": Count("validation_exceptions", filter=Q(validation_exceptions__severity=sev))
                         for sev in ValidationSeverity.values}))


FILE_ORDERING = {"uploaded_at": "uploaded_at", "business_date": "business_date", "filename": "filename",
                 "processing_status": "processing_status", "file_type": "file_type_config__file_type_code"}


def _ordering(value: str | None, allowed: dict, default: tuple):
    if not value:
        return default, None
    out = []
    for part in value.split(","):
        part = part.strip()
        field = allowed.get(part.lstrip("-"))
        if field is None:
            return None, f"Unknown ordering {part!r}; allowed: {', '.join(sorted(allowed))} (prefix - to reverse)"
        out.append(f"-{field}" if part.startswith("-") else field)
    return (*out, "-pk"), None


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------
class FileDetectView(OpsView):
    """POST multipart: files=<file or its first 64 KB> (repeated, same order
    as the user's list), optional sha256=<hex> (repeated, same order; "" for
    none). Nothing is saved."""

    def post(self, request, module):
        files = request.FILES.getlist("files")
        if not files:
            return _bad_request("Invalid input", {"files": ["Choose at least one file."]})
        hashes = request.data.getlist("sha256") if hasattr(request.data, "getlist") else []
        chosen = None
        if request.data.get("business_date"):  # UI refinements 2.1: the date chosen on the review step
            chosen = _iso_date(request.data.get("business_date"))
            if chosen is None:
                return _bad_request("Invalid input", {"business_date": ["Enter a date as YYYY-MM-DD."]})
        items = [intake.DetectInput(name=f.name, head=f.read(intake.DETECT_HEAD_BYTES),
                                    sha256=hashes[i] if i < len(hashes) else "")
                 for i, f in enumerate(files)]
        try:
            body = intake.detect_files(request.user, self.module, items, business_date=chosen)
        except OperationError as exc:
            return _error(exc)
        return Response(body)


class FileListView(OpsView):
    def get(self, request, module):
        p, errors = request.query_params, {}
        qs = _files(self.module)
        if p.get("business_date"):
            d = _iso_date(p["business_date"])
            if d is None:
                errors["business_date"] = ["Enter a date as YYYY-MM-DD."]
            else:
                qs = qs.filter(Q(business_date=d) | Q(business_date__isnull=True, batch_business_date=d))
        if p.get("ids"):  # UI refinements 2.2: poll the files of one upload
            ids = [i.strip() for i in p["ids"].split(",") if i.strip()]
            if not ids or not all(i.isdigit() for i in ids):
                errors["ids"] = ["A comma list of file ids."]
            else:
                qs = qs.filter(pk__in=ids)
        if p.get("file_type"):
            qs = qs.filter(file_type_config__file_type_code__in=p["file_type"].split(","))
        if p.get("file_type_config"):
            ids = p["file_type_config"].split(",")
            if not all(i.isdigit() for i in ids):
                errors["file_type_config"] = ["A comma list of ids."]
            else:
                qs = qs.filter(file_type_config_id__in=ids)
        if p.get("cycle"):
            qs = qs.filter(settlement_cycle__cycle_code__in=p["cycle"].split(","))
        if p.get("status"):
            wanted = p["status"].split(",")
            bad = [s for s in wanted if s not in ProcessingStatus.values]
            if bad:
                errors["status"] = [f"Unknown status {', '.join(bad)}; allowed: {', '.join(ProcessingStatus.values)}"]
            qs = qs.filter(processing_status__in=wanted)
        if p.get("current") in ("true", "false"):
            qs = qs.filter(is_current=p["current"] == "true")
        order, order_error = _ordering(p.get("ordering"), FILE_ORDERING, ("-uploaded_at", "-pk"))
        if order_error:
            errors["ordering"] = [order_error]
        if errors:
            return _bad_request("Invalid input", errors)
        paginator = legacy.QueuePagination()
        page = paginator.paginate_queryset(qs.order_by(*order), request, view=self)
        response = paginator.get_paginated_response([file_row(f) for f in page])
        # So the screen knows whether to keep refreshing (design 4.9).
        response.data["processing_count"] = UploadedFile.objects.filter(
            payment_module=self.module, processing_status__in=intake.PROCESSING_STATUSES).count()
        return response

    def post(self, request, module):
        """Multipart: business_date=YYYY-MM-DD, files=<file> (repeated),
        items=<JSON list, same order as files: {"file_type_config_id": int,
        "cycle": "1C" | null}>."""
        errors = {}
        d = _iso_date(request.data.get("business_date"))
        if d is None:
            errors["business_date"] = ["Enter the business date as YYYY-MM-DD."]
        files = request.FILES.getlist("files")
        if not files:
            errors["files"] = ["Choose at least one file."]
        try:
            items = json.loads(request.data.get("items") or "[]")
        except (TypeError, ValueError):
            items = None
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            errors["items"] = ["A JSON list with one {file_type_config_id, cycle} object per file."]
        elif files and len(items) != len(files):
            errors["items"] = [f"{len(items)} item(s) for {len(files)} file(s); give one per file."]
        if errors:
            return _bad_request("Invalid input", errors)
        upload_items = [intake.UploadItem(name=f.name, file=f, file_type_config_id=i.get("file_type_config_id"),
                                          cycle=(i.get("cycle") or None))
                        for f, i in zip(files, items)]
        try:
            outcome = intake.upload_files(request.user, self.module, d, upload_items, source="api")
        except OperationError as exc:
            return _error(exc)
        accepted = list(_files(self.module).filter(pk__in=[f.pk for f in outcome.accepted]).order_by("pk"))
        body = {"business_date": d, "accepted": [file_row(f) for f in accepted], "refused": outcome.refused}
        if not accepted:
            body["detail"] = "No file was accepted."
            return Response(body, status=status.HTTP_400_BAD_REQUEST)
        return Response(body, status=status.HTTP_201_CREATED)


def _file_detail(module, pk) -> dict:
    f = get_object_or_404(_files(module), pk=pk)
    body = file_row(f)
    history = list(OperationLog.objects.filter(payment_module=module, uploaded_file_id=f.pk).order_by("created_at", "id"))
    body["history"] = _history(history)
    # UI refinements 4.6: the original's availability and who downloaded it, newest first.
    _path, reason, message = original_path(f)
    body["original"] = {"available": reason is None, "reason": reason, "message": message,
                        "download_url": (f"/api/v1/{module.code.lower()}/files/{f.pk}/original/"
                                         if reason is None else None)}
    downloads = [{"by": e.username or None, "at": e.created_at} for e in reversed(history)
                 if e.operation == OperationType.ORIGINAL_FILE_DOWNLOADED]
    body["downloads"] = downloads
    body["download_count"] = len(downloads)
    return body


# ---------------------------------------------------------------------------
# the stored original (UI refinements 4.6, contract B2)
# ---------------------------------------------------------------------------
NOT_STORED, MISSING, CHANGED = "NOT_STORED", "MISSING", "CHANGED"
ORIGINAL_MESSAGES = {
    NOT_STORED: "The original of this file is not stored by the app (it was loaded outside the upload page).",
    MISSING: "The stored original of this file is missing on the server.",
    CHANGED: "The stored original of this file has changed since it was uploaded (size differs); it is not served.",
}


# A fixed table, not mimetypes: on Windows mimetypes reads the registry, which may
# map .csv to an Excel type.
CONTENT_TYPES = {".csv": "text/csv", ".txt": "text/plain", ".xls": "application/vnd.ms-excel",
                 ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 ".zip": "application/zip", ".xml": "application/xml"}


def original_path(f: UploadedFile) -> tuple[Path | None, str | None, str]:
    """(path, None, "") when the stored upload may be served, else (None,
    reason, message). Path safety: only a file whose resolved path (symlinks
    followed) lies inside FILE_PROCESSING_WORK_DIR/uploads/ is ever served."""
    root = intake.upload_root()
    try:
        path = Path(f.path).resolve() if f.path else None
    except (OSError, ValueError):
        path = None
    if path is None or not path.is_relative_to(root) or path == root:
        return None, NOT_STORED, ORIGINAL_MESSAGES[NOT_STORED]
    if not path.is_file():
        return None, MISSING, ORIGINAL_MESSAGES[MISSING]
    if f.size_bytes and path.stat().st_size != f.size_bytes:
        return None, CHANGED, ORIGINAL_MESSAGES[CHANGED]
    return path, None, ""


class FileOriginalView(OpsView):
    """GET: the stored upload exactly as uploaded. All five roles; every
    download is written to core.OperationLog (ORIGINAL_FILE_DOWNLOADED)."""

    def get(self, request, module, pk):
        f = UploadedFile.objects.filter(payment_module=self.module, pk=pk).first()
        if f is None:
            return Response({"detail": "No such file."}, status=status.HTTP_404_NOT_FOUND)
        path, reason, message = original_path(f)
        if reason is not None:
            code = status.HTTP_404_NOT_FOUND if reason == NOT_STORED else status.HTTP_410_GONE
            return Response({"detail": message, "reason": reason}, status=code)
        handle = open(path, "rb")  # noqa: SIM115 - FileResponse closes it
        log_operation(OperationType.ORIGINAL_FILE_DOWNLOADED, user=request.user, payment_module=self.module,
                      business_date=f.business_date or f.batch_business_date, uploaded_file_id=f.pk, source="api",
                      details={"filename": f.filename, "sha256": f.sha256_hash, "size_bytes": f.size_bytes})
        content_type = CONTENT_TYPES.get(Path(f.filename).suffix.lower(), "application/octet-stream")
        response = FileResponse(handle, as_attachment=True, filename=f.filename, content_type=content_type)
        response["X-Content-SHA256"] = f.sha256_hash
        response["Cache-Control"] = "no-store"
        return response


class FileDetailView(OpsView):
    def get(self, request, module, pk):
        return Response(_file_detail(self.module, pk))


EXCEPTION_ORDERING = {"detected_at": "detected_at", "severity": "severity", "rule_code": "rule_code",
                      "row_reference": "row_reference"}


class FileExceptionsView(OpsView):
    def get(self, request, module, pk):
        f = get_object_or_404(UploadedFile.objects.filter(payment_module=self.module), pk=pk)
        p, errors = request.query_params, {}
        qs = FileValidationException.objects.filter(uploaded_file=f)
        if p.get("severity"):
            wanted = p["severity"].split(",")
            if any(s not in ValidationSeverity.values for s in wanted):
                errors["severity"] = [f"Allowed: {', '.join(ValidationSeverity.values)}"]
            qs = qs.filter(severity__in=wanted)
        if p.get("rule_code"):
            qs = qs.filter(rule_code__in=p["rule_code"].split(","))
        order, order_error = _ordering(p.get("ordering"), EXCEPTION_ORDERING, ("detected_at", "pk"))
        if order_error:
            errors["ordering"] = [order_error]
        if errors:
            return _bad_request("Invalid input", errors)
        paginator = legacy.QueuePagination()
        page = paginator.paginate_queryset(qs.order_by(*order), request, view=self)
        return paginator.get_paginated_response([
            {"id": e.pk, "rule_code": e.rule_code, "severity": e.severity, "message": e.message,
             "row_reference": e.row_reference, "detected_at": e.detected_at, "resolved": e.resolved}
            for e in page])


class FileSelectDateView(OpsView):
    """POST {"business_date": "YYYY-MM-DD"} (one of date_selection.detected_dates)."""

    def post(self, request, module, pk):
        f = get_object_or_404(UploadedFile.objects.filter(payment_module=self.module), pk=pk)
        chosen = request.data.get("business_date")
        if not chosen or _iso_date(chosen) is None:
            return _bad_request("Invalid input", {"business_date": ["Enter the business date as YYYY-MM-DD."]})
        try:
            intake.select_business_date(request.user, f, chosen, source="api")
        except OperationError as exc:
            return _error(exc)
        return Response(_file_detail(self.module, pk))


# ---------------------------------------------------------------------------
# business dates
# ---------------------------------------------------------------------------
def _date_row(overview: dict) -> dict:
    return {k: v for k, v in overview.items() if not k.startswith("_")}


class DateListView(OpsView):
    def get(self, request, module):
        p, errors = request.query_params, {}
        dates = business_dates(self.module)
        for name, keep in (("date_from", lambda d, x: d >= x), ("date_to", lambda d, x: d <= x)):
            if p.get(name):
                x = _iso_date(p[name])
                if x is None:
                    errors[name] = ["Enter a date as YYYY-MM-DD."]
                else:
                    dates = [d for d in dates if keep(d, x)]
        if p.get("ordering") not in (None, "", "business_date", "-business_date"):
            errors["ordering"] = ["Allowed: business_date, -business_date"]
        if errors:
            return _bad_request("Invalid input", errors)
        if p.get("ordering") == "business_date":
            dates = list(reversed(dates))
        paginator = legacy.QueuePagination()
        page = paginator.paginate_queryset(dates, request, view=self)
        overviews = date_overviews(self.module, list(page))
        files = date_file_summaries(self.module, list(page))
        rows = {d: {**_date_row(overviews[d]), "sskupi": files[d]} for d in page}
        finish_date_statuses(self.module, rows, files)  # DONE -> FILE_PENDING (owner Q2)
        return paginator.get_paginated_response([rows[d] for d in page])


class _DateMixin:
    def business_date(self, value):
        d = _iso_date(value)
        if d is None:
            return None, _bad_request("Invalid input", {"business_date": ["Enter a date as YYYY-MM-DD."]})
        return d, None


class DateDetailView(_DateMixin, OpsView):
    def get(self, request, module, business_date):
        d, error = self.business_date(business_date)
        if error:
            return error
        if d not in set(business_dates(self.module)):
            return Response({"detail": "No files or runs for this date."}, status=status.HTTP_404_NOT_FOUND)
        overview = date_overviews(self.module, [d])[d]
        body = _date_row(overview)
        files = date_file_summaries(self.module, [d])
        body["sskupi"] = files[d]
        finish_date_statuses(self.module, {d: body}, files)  # DONE -> FILE_PENDING (owner Q2)
        body["checklist"] = overview["_checklist"]["slots"]
        body["unexpected_files"] = overview["_checklist"]["unexpected"]
        body["archived"] = intake.date_is_archived(self.module, d)
        body["runs"] = [run_payload(r) for r in ReconciliationRun.objects.filter(
            payment_module=self.module, business_date=d).order_by("-requested_at", "-id")[:20]]
        body["history"] = _history(OperationLog.objects.filter(payment_module=self.module, business_date=d)
                                   .order_by("-created_at", "-id")[:100])
        return Response(body)


class DateRunView(_DateMixin, OpsView):
    """POST (no body): queue presence matrix -> rule evaluation + case sync
    -> settlement for the date. 202 with the run; poll GET /dates/<date>/."""

    def post(self, request, module, business_date):
        d, error = self.business_date(business_date)
        if error:
            return error
        try:
            run = start_run(request.user, self.module, d, source="api")
        except OperationError as exc:
            return _error(exc)
        return Response(run_payload(run), status=status.HTTP_202_ACCEPTED)
