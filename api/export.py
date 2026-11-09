"""
Report downloads (Phase 6; docs/phase6_api_contract.txt section A and RR3,
owner answers D1, D2, D6). One shared writer for every report:

  - ?export=csv | xlsx on a report endpoint returns the file instead of the
    JSON answer, with the same filters and no paging; sheet=<key> picks the
    sheet of a CSV (one sheet per file).
  - CSV: UTF-8 with BOM, CRLF line ends, streamed row by row
    (StreamingHttpResponse). Always available.
  - Excel: written with openpyxl in write-only mode to a spooled temporary
    file, then streamed. openpyxl is NOT approved by bank IT yet and is not
    in requirements.txt: until it is installed, export.formats is ["csv"]
    and export=xlsx is refused with 400. Installing the package switches it
    on; no code change.
  - At most EXPORT_LIMIT rows per sheet (R8): above it 400 "narrow the
    filters".
  - Every successful download is written to core.OperationLog as
    REPORT_EXPORTED (R10), when the response is built. Refused downloads
    are not logged (D6).

File content: a header block (bank, module, report, filters, produced by /
at, versions), one blank row, the column headers, the rows. RRNs stay text
(CSV ="<rrn>", D2; Excel string cells). Text starting with = + - @ TAB CR
gets a leading apostrophe (formula injection); amounts and counts are
numbers and never prefixed.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import tempfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from django.conf import settings
from django.http import FileResponse, StreamingHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from core.models import OperationType
from core.operations import log_operation

EXPORT_LIMIT = 100_000
EXPORT_FORMATS = ("xlsx", "csv")
CSV_CONTENT_TYPE = "text/csv; charset=utf-8"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSX_UNAVAILABLE = "Excel download is not available on this server; use CSV."
UNKNOWN_FORMAT = "Unknown export format."
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_CSV_FLUSH_ROWS = 500

# Column kinds.
TEXT, RRN, AMOUNT, COUNT, DATE, DATETIME, BOOL = "text", "rrn", "amount", "count", "date", "datetime", "bool"


def xlsx_available() -> bool:
    return importlib.util.find_spec("openpyxl") is not None


def export_formats() -> list[str]:
    """What this server can write now (RR3): ["csv"] until openpyxl is installed."""
    return ["xlsx", "csv"] if xlsx_available() else ["csv"]


@dataclass
class Column:
    key: str
    header: str
    kind: str = TEXT


@dataclass
class Sheet:
    """One table of a report. rows() is called only for a download and
    yields one dict per row (column key -> value)."""

    key: str
    label: str
    columns: list[Column]
    rows: Callable[[], Iterable[dict]]
    row_count: int
    extra: dict = field(default_factory=dict)


def export_block(sheets: list[Sheet], main_key: str | None = None) -> dict:
    """RR3. row_count is the main sheet's: the first, or main_key (the CSV default)."""
    main = next((s for s in sheets if s.key == main_key), sheets[0])
    return {"formats": export_formats(), "row_count": main.row_count, "limit": EXPORT_LIMIT,
            "allowed": max(s.row_count for s in sheets) <= EXPORT_LIMIT,
            "sheets": [{"key": s.key, "label": s.label, "row_count": s.row_count} for s in sheets]}


def wants_export(request) -> bool:
    return "export" in request.query_params


# ---------------------------------------------------------------------------
# values
# ---------------------------------------------------------------------------
def safe_text(value) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_START) else text


def _plain(value):
    """JSON-safe copy of the report filters for the operations log."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _filter_text(filters: dict) -> str:
    parts = []
    for k, v in filters.items():
        if v is None or v == [] or v == "":
            v = "all"
        elif isinstance(v, bool):
            v = "yes" if v else "no"
        elif isinstance(v, (list, tuple)):
            v = ", ".join(str(x) for x in v)
        elif isinstance(v, (date, datetime)):
            v = v.isoformat()
        parts.append(f"{k}: {v}")
    return "; ".join(parts)


def header_rows(report: dict) -> list[list[str]]:
    v = report.get("versions") or {}
    generated = timezone.localtime(report["generated_at"]) if report.get("generated_at") else timezone.localtime()
    return [
        ["Bank", settings.REPORT_BANK_NAME],
        ["Module", (report.get("module") or "").upper() or "All modules"],
        ["Report", report["title"]],
        ["Filters", _filter_text(report.get("filters") or {})],
        ["Produced by", report.get("generated_by") or ""],
        ["Produced at (IST)", generated.strftime("%d-%b-%Y %H:%M:%S")],
        ["Rule set versions", ", ".join(v.get("rule_sets") or []) or "none"],
        ["NTSL mapping versions", ", ".join(v.get("ntsl_mappings") or []) or "none"],
        ["Tolerance versions", ", ".join(v.get("tolerances") or []) or "none"],
    ]


def _csv_cell(kind: str, value) -> str:
    if value is None or value == "":
        return ""
    if kind == RRN:
        return '="{}"'.format(str(value).replace('"', '""'))
    if kind == AMOUNT:
        return f"{Decimal(str(value)):.2f}"
    if kind == COUNT:
        return str(int(value))
    if kind == BOOL:
        return "Yes" if value else "No"
    if kind == DATETIME and isinstance(value, datetime):
        return timezone.localtime(value).isoformat() if timezone.is_aware(value) else value.isoformat()
    if kind == DATE and isinstance(value, date):
        return value.isoformat()
    return safe_text(value)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def _csv_stream(report: dict, sheet: Sheet) -> Iterator[bytes]:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    for row in header_rows(report):
        writer.writerow([safe_text(x) for x in row])
    writer.writerow([])
    writer.writerow([c.header for c in sheet.columns])
    yield ("﻿" + buf.getvalue()).encode("utf-8")
    buf.seek(0)
    buf.truncate()
    n = 0
    for row in sheet.rows():
        writer.writerow([_csv_cell(c.kind, row.get(c.key)) for c in sheet.columns])
        n += 1
        if n % _CSV_FLUSH_ROWS == 0:
            yield buf.getvalue().encode("utf-8")
            buf.seek(0)
            buf.truncate()
    if buf.tell():
        yield buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# Excel (only when openpyxl is installed)
# ---------------------------------------------------------------------------
AMOUNT_FORMAT = "#,##,##0.00"  # Indian grouping
DATE_FORMAT = "dd-mmm-yyyy"
DATETIME_FORMAT = "dd-mmm-yyyy hh:mm"


def _xlsx_cell(ws, kind: str, value):
    from openpyxl.cell import WriteOnlyCell

    if value is None or value == "":
        return None
    if kind == AMOUNT:
        cell = WriteOnlyCell(ws, value=Decimal(str(value)))
        cell.number_format = AMOUNT_FORMAT
        return cell
    if kind == COUNT:
        return int(value)
    if kind == BOOL:
        return "Yes" if value else "No"
    if kind == DATETIME and isinstance(value, datetime):
        local = timezone.localtime(value) if timezone.is_aware(value) else value
        cell = WriteOnlyCell(ws, value=local.replace(tzinfo=None))
        cell.number_format = DATETIME_FORMAT
        return cell
    if kind == DATE and isinstance(value, date):
        cell = WriteOnlyCell(ws, value=value)
        cell.number_format = DATE_FORMAT
        return cell
    text = str(value) if kind == RRN else safe_text(value)
    cell = WriteOnlyCell(ws, value=text)
    cell.data_type = "s"  # never a formula
    return cell


def _xlsx_file(report: dict, sheets: list[Sheet]):
    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    for sheet in sheets:
        ws = wb.create_sheet(title=sheet.label[:31])
        for row in header_rows(report):
            ws.append([_xlsx_cell(ws, TEXT, x) for x in row])
        ws.append([])
        ws.append([_xlsx_cell(ws, TEXT, c.header) for c in sheet.columns])
        for row in sheet.rows():
            ws.append([_xlsx_cell(ws, c.kind, row.get(c.key)) for c in sheet.columns])
    tmp = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)  # noqa: SIM115 - FileResponse closes it
    wb.save(tmp)
    tmp.seek(0)
    return tmp


# ---------------------------------------------------------------------------
# the download
# ---------------------------------------------------------------------------
def _refused(errors: dict, **extra) -> Response:
    return Response({"detail": next(iter(errors.values()))[0], "errors": errors, **extra},
                    status=status.HTTP_400_BAD_REQUEST)


def export_response(request, *, report: dict, module, sheets: list[Sheet], file_base: str,
                    default_sheet: str | None = None):
    """The file for ?export=, or a 400 (not logged). file_base is the name
    without sheet and extension, e.g. "UPI_settlement_2026-07-01_to_2026-07-31"."""
    p = request.query_params
    fmt = p.get("export")
    if fmt not in EXPORT_FORMATS:
        return _refused({"export": [UNKNOWN_FORMAT]})
    if fmt == "xlsx" and not xlsx_available():
        return _refused({"export": [XLSX_UNAVAILABLE]})
    by_key = {s.key: s for s in sheets}
    sheet_key = None
    if fmt == "csv":
        sheet_key = p.get("sheet") or default_sheet or sheets[0].key
        if sheet_key not in by_key:
            return _refused({"sheet": [f"Unknown sheet; allowed: {', '.join(by_key)}"]})
        chosen = [by_key[sheet_key]]
    else:
        chosen = sheets
    largest = max(s.row_count for s in chosen)
    if largest > EXPORT_LIMIT:
        return _refused({"export": [f"{largest:,} rows is more than the {EXPORT_LIMIT:,} a download may hold; "
                                    "narrow the filters."]}, row_count=largest, limit=EXPORT_LIMIT)
    suffix = f"_{sheet_key}" if fmt == "csv" and len(sheets) > 1 else ""
    file_name = f"{file_base}{suffix}.{fmt}"
    row_count = chosen[0].row_count
    if fmt == "csv":
        response = StreamingHttpResponse(_csv_stream(report, chosen[0]), content_type=CSV_CONTENT_TYPE)
    else:
        response = FileResponse(_xlsx_file(report, sheets), content_type=XLSX_CONTENT_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{file_name}"'
    response["Cache-Control"] = "no-store"
    log_operation(OperationType.REPORT_EXPORTED, user=request.user, payment_module=module, source="api",
                  details={"report": report["key"], "format": fmt, "sheet": sheet_key,
                           "filters": _plain(report.get("filters") or {}), "row_count": row_count,
                           "file_name": file_name})
    return response


def range_file_base(module, stem: str, d_from, d_to) -> str:
    prefix = f"{module.code}_" if module is not None else ""
    return f"{prefix}{stem}_{d_from.isoformat()}_to_{d_to.isoformat()}"
