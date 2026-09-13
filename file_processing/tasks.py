"""
Celery pipeline for inbound source-file processing (approved plan §5).

All four stages are implemented: 1 `format_sniff_and_parse`,
2 `validate_structure_and_content`, 3 `normalize_rows`,
4 `load_to_unified_table` (populates `transactions.TransactionRecord` --
added 2026-09-10, see TRACKER.md's Phase 1 exit criteria and doubts.md's
"Resolved" section for the facts this stage's per-file-type extraction
logic is built against). In scope for Stage 4: CBS GL Extract, Raw Data,
Merchant Raw Data (ACQ/ISS), Merchant Raw Data BBP, DRC, Timeout,
Adjustment -- NTSL is deliberately excluded (settlement summary/totals
report, no RRN column, needs its own design; see doubts.md).

Each stage is a separate Celery task chained together (`build_processing_
chain`) so it's independently retryable/visible, rather than one large
task, matching the approved plan.

KNOWN SIMPLIFICATION (explicitly approved as an interim measure, not a
resolved design): the approved plan's stage 2 says parsing writes "into a
staging table keyed by uploaded_file_id", but no such staging model is in
this task's scope (only UploadedFile/FileTypeConfig/FileValidationException
were approved to build -- a real ParsedRow-style staging table is likely to
be scoped alongside TransactionRecord later). Here, parsed/validated/
normalized rows are instead passed from stage to stage as each Celery
task's JSON-serializable return value, relying on Celery's normal chain
mechanism (broker/result-backend transport) rather than a durable,
independently-queryable table. This is fine for moderate files but means a
~150k-row CBS file's rows travel through Redis as one serialized payload
per stage -- acceptable for this phase, not a substitute for real staging
storage. Row dict cell keys are stringified (`{"1": ..., "3": ...}`) since
JSON object keys are always strings.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from celery import chain, shared_task
from django.utils.dateparse import parse_datetime

from core.models import Direction
from file_processing.models import (
    DateValidationMode,
    FileValidationException,
    ProcessingStatus,
    UploadedFile,
    ValidationSeverity,
)
from file_processing.parsers.cbs_spreadsheetml import parse_spreadsheetml
from file_processing.parsers.csv_parser import parse_csv
from file_processing.parsers.ntsl_ooxml import parse_ooxml_workbook
from file_processing.parsers.pagination_strip import strip_pagination_artifacts
from file_processing.parsers.sniff import PayloadKind, sniff_path
from transactions.models import TransactionRecord

logger = logging.getLogger(__name__)

# CBS remarks pattern: "By UPI/<RRN>/<VPA>/<description>" (credit) or
# "To UPI/<RRN>/<VPA>/<description>" (debit) -- confirmed directly against
# 14072026/cbs_report/b5rg090oa.xls. This is the concrete mechanism behind
# FileTypeConfig.rrn_extraction_regex / direction_derivation_rule for CBS
# specifically; other file types' rules are applied generically below via
# FileTypeConfig fields rather than this CBS-specific constant.
_CBS_REMARKS_RE = re.compile(r"^(By|To)\s+UPI/([^/]+)/")


def build_processing_chain(uploaded_file_id: int):
    """Assemble the Celery chain for one UploadedFile.

    For EXACT_MATCH file types this is the full stage 1-4 chain. For
    RANGE_CONTAINS file types (e.g. CBS GL Extract) the chain stops after
    validate_structure_and_content: that stage pauses processing at
    AWAITING_DATE_SELECTION once it has detected the file's distinct row
    dates, and normalize_rows + load_to_unified_table are instead invoked
    later as their own standalone chain -- from the "Select business date"
    admin action, see file_processing/admin.py -- once a human has picked
    one of them."""
    date_validation_mode = (
        UploadedFile.objects.select_related("file_type_config")
        .get(pk=uploaded_file_id)
        .file_type_config.date_validation_mode
    )
    if date_validation_mode == DateValidationMode.RANGE_CONTAINS:
        return chain(
            format_sniff_and_parse.s(uploaded_file_id),
            validate_structure_and_content.s(),
        )
    return chain(
        format_sniff_and_parse.s(uploaded_file_id),
        validate_structure_and_content.s(),
        normalize_rows.s(),
        load_to_unified_table.s(),
    )


def _record_exception(
    uploaded_file: UploadedFile,
    rule_code: str,
    message: str,
    *,
    severity: str = ValidationSeverity.ERROR,
    row_reference: str = "",
) -> None:
    FileValidationException.objects.create(
        uploaded_file=uploaded_file,
        rule_code=rule_code,
        severity=severity,
        message=message,
        row_reference=row_reference,
    )


def _resolve_column_mapping(config, worksheet_name: str | None) -> dict[str, Any]:
    """Resolves `FileTypeConfig.column_mapping` to a flat {field_name: col_ref}
    dict for one specific worksheet.

    CBS GL Extract's ~154,661-row report is split across 3 worksheets
    ("Report", "Report1", "Report2") purely by Excel's row-count limit --
    one continuous report, not 3 separate reports -- but each worksheet
    uses its own stable column layout (confirmed by scanning every row's
    `ss:Index` shape across the full real file, not a sample; see this
    backend engineer's handoff report). A single flat column_mapping can
    only describe one of the three, silently causing every row from the
    other two worksheets to be unresolvable (skipped, not crashed) --
    that's why CBS previously reached ProcessingStatus.LOADED with 0 rows.

    Two supported `column_mapping` shapes, detected structurally so the six
    other (single-worksheet, single-layout) file types stay on the existing
    flat form unchanged:
      - New per-worksheet form: every field's value is itself a dict of
        {worksheet_name: col_ref}, e.g.
        {"date": {"Report": 1, "Report1": 1, "_default": 1}, ...}.
        Looked up by `worksheet_name`, falling back to that field's
        "_default" entry if the sheet name isn't recognized (e.g. an
        unexpected 4th overflow worksheet).
      - Existing flat form: {field_name: col_ref} directly, used as-is --
        this is every other file type's config today (DRC, Timeout,
        Adjustment, Raw Data, Merchant Raw Data, Merchant Raw Data BBP),
        none of which are multi-worksheet.
    """
    mapping = config.column_mapping or {}
    if mapping and all(isinstance(v, dict) for v in mapping.values()):
        resolved: dict[str, Any] = {}
        for field_name, per_worksheet in mapping.items():
            if worksheet_name in per_worksheet:
                resolved[field_name] = per_worksheet[worksheet_name]
            elif "_default" in per_worksheet:
                resolved[field_name] = per_worksheet["_default"]
        return resolved
    return mapping


def _rows_to_json_safe(rows: list, *, row_number_attr: str = "row_number") -> list[dict[str, Any]]:
    """Convert parser `ParsedRow`-like dataclass instances (int-keyed
    `cells` dict) into plain, JSON-serializable dicts (string-keyed) for
    passing as a Celery task's return value/next-task argument."""
    out = []
    for row in rows:
        out.append(
            {
                "row_number": getattr(row, row_number_attr),
                "worksheet": getattr(row, "worksheet_name", None) or getattr(row, "sheet_name", None),
                "cells": {str(k): v for k, v in row.cells.items()},
            }
        )
    return out


# ---------------------------------------------------------------------------
# Stage 1: format_sniff_and_parse
# ---------------------------------------------------------------------------
@shared_task
def format_sniff_and_parse(uploaded_file_id: int) -> dict[str, Any]:
    uploaded_file = UploadedFile.objects.select_related("file_type_config").get(pk=uploaded_file_id)
    config = uploaded_file.file_type_config
    path = Path(uploaded_file.path)

    sniffed = sniff_path(path)
    detected = sniffed.payload.value if sniffed.payload else ""
    uploaded_file.detected_format = detected

    if sniffed.payload is None or sniffed.payload.value != config.payload_format:
        _record_exception(
            uploaded_file,
            "FORMAT_MISMATCH",
            f"Detected format '{detected or sniffed.container.value}' does not match "
            f"configured payload_format '{config.payload_format}' for file type "
            f"'{config.file_type_code}' (container={sniffed.container.value}, {sniffed.detail})",
        )
        uploaded_file.processing_status = ProcessingStatus.VALIDATION_FAILED
        uploaded_file.save(update_fields=["detected_format", "processing_status"])
        return {"uploaded_file_id": uploaded_file_id, "rows": [], "halted": True}

    if sniffed.payload == PayloadKind.SPREADSHEETML_XML:
        rows = _rows_to_json_safe(list(parse_spreadsheetml(path)))
    elif sniffed.payload == PayloadKind.OOXML_ZIP:
        rows = _rows_to_json_safe(list(parse_ooxml_workbook(path)))
    elif sniffed.payload == PayloadKind.CSV:
        rows = _rows_to_json_safe(
            list(
                parse_csv(
                    path,
                    delimiter=config.delimiter,
                    encoding=config.encoding,
                    has_header=config.has_header,
                )
            )
        )
    else:
        _record_exception(uploaded_file, "UNSUPPORTED_FORMAT", f"No parser available for {sniffed.payload}")
        uploaded_file.processing_status = ProcessingStatus.FAILED
        uploaded_file.save(update_fields=["detected_format", "processing_status"])
        return {"uploaded_file_id": uploaded_file_id, "rows": [], "halted": True}

    uploaded_file.processing_status = ProcessingStatus.PARSED
    uploaded_file.save(update_fields=["detected_format", "processing_status"])
    return {"uploaded_file_id": uploaded_file_id, "rows": rows, "halted": False}


# Date formats confirmed present across the real sample files this pipeline
# handles:
#   - CBS GL Extract (14072026/cbs_report/b5rg090oa.xls, all 154,661 rows
#     across its 3 worksheets audited): every genuine data row's date cell
#     is an ISO datetime-with-"T" string, e.g. "2026-07-13T00:00:00.000".
#   - DRC / Timeout ("TXN Date" column): "DD/MM/YYYY", e.g. "14/07/2026".
#   - Adjustment ("Adjdate" column): "DD-MM-YYYY", e.g. "14-07-2026" --
#     confirmed to genuinely vary per row (not just per file), unlike the
#     other EXACT_MATCH file types -- see doubts.md's Adjustment resolution.
# A bare "YYYY-MM-DD" (no "T") is handled defensively in case a future
# export/file type presents it, but is not otherwise exercised by any
# current sample.
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DMY_DASH_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")
_DMY_SLASH_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def _row_date_iso(cells: dict[str, Any], date_col: str) -> str | None:
    """Shared by validate_structure_and_content (RANGE_CONTAINS distinct-date
    detection) and normalize_rows (RANGE_CONTAINS row filtering, and Stage 4's
    per-row business_date resolution for EXACT_MATCH types whose row-level
    date can genuinely differ from the file's nominal business date, e.g.
    Adjustment's Adjdate).

    Returns the row's date normalized to canonical ISO YYYY-MM-DD, or None
    if the cell is missing/empty or does not represent a real, valid date
    (e.g. a stray column-header label like "Date" that slipped past
    pagination stripping -- see pagination_strip.py). Never falls back to
    blindly slicing an arbitrary string, since that would silently
    fabricate a fake date out of non-date input.

    Format is inferred from the raw string's shape (presence of "T", "/",
    or "-") rather than taken from a per-`FileTypeConfig` format field --
    the four formats above are mutually unambiguous by separator/digit
    grouping, so no config field is needed to disambiguate them."""
    if not date_col or not cells.get(date_col):
        return None
    raw_date = str(cells[date_col]).strip()
    if "T" in raw_date:
        parsed = parse_datetime(raw_date)
        return parsed.date().isoformat() if parsed else None
    if _DATE_ONLY_RE.match(raw_date):
        try:
            return datetime.strptime(raw_date, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None
    if _DMY_DASH_RE.match(raw_date):
        # Adjustment's "Adjdate" -- DD-MM-YYYY. Checked before the "/" form
        # below only for clarity; the two patterns can't collide (different
        # separators) so order between them doesn't actually matter, but
        # _DATE_ONLY_RE above must stay first since a YYYY-MM-DD string also
        # contains "-" but is a different digit grouping (4-2-2 vs 2-2-4).
        try:
            return datetime.strptime(raw_date, "%d-%m-%Y").date().isoformat()
        except ValueError:
            return None
    if _DMY_SLASH_RE.match(raw_date):
        # DRC / Timeout's "TXN Date" -- DD/MM/YYYY.
        try:
            return datetime.strptime(raw_date, "%d/%m/%Y").date().isoformat()
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Stage 2: validate_structure_and_content
# ---------------------------------------------------------------------------
@shared_task
def validate_structure_and_content(context: dict[str, Any]) -> dict[str, Any]:
    if context.get("halted"):
        return context

    uploaded_file_id = context["uploaded_file_id"]
    uploaded_file = UploadedFile.objects.select_related("file_type_config").get(pk=uploaded_file_id)
    config = uploaded_file.file_type_config
    rows = context["rows"]

    # Pagination/header/footer stripping happens first -- otherwise header/
    # footer rows would spuriously fail the mandatory-column checks below
    # (see pagination_strip.py docstring for why this is content-pattern-,
    # not row-count-, based). strip_pagination_artifacts() takes plain
    # cell-value sequences and preserves order without reporting *which*
    # rows survived, so a unique per-row marker is appended (after the
    # last real cell, so it can't interfere with `^`-anchored patterns
    # matching the row's actual leading content) purely to recover row
    # identity afterwards -- the matching logic itself stays in
    # pagination_strip.py, not duplicated here.
    # `strip_pagination_artifacts` preserves each surviving row's original
    # list object (see its `kept.append(list(row))`), so an int marker
    # appended here comes back unchanged and can be used to recover row
    # identity directly, with no string-parsing needed.
    marked_cell_lists = [[*row["cells"].values(), i] for i, row in enumerate(rows)]
    kept_cell_lists, stripped_count = strip_pagination_artifacts(
        marked_cell_lists, config.pagination_artifact_patterns
    )
    kept_indices = {cl[-1] for cl in kept_cell_lists}
    kept_rows = [row for i, row in enumerate(rows) if i in kept_indices]

    if not kept_rows:
        _record_exception(
            uploaded_file,
            "NO_DATA_ROWS",
            f"No data rows remained after stripping {stripped_count} pagination/header/footer "
            f"rows out of {len(rows)} parsed rows",
        )
        uploaded_file.processing_status = ProcessingStatus.VALIDATION_FAILED
        uploaded_file.save(update_fields=["processing_status"])
        return {"uploaded_file_id": uploaded_file_id, "rows": [], "halted": True}

    # Mandatory-field presence check. column_mapping maps canonical field
    # name -> source column identifier (a cell index, as a string, for the
    # spreadsheet-style parsers used so far) -- resolved per-row's worksheet
    # for multi-worksheet, per-worksheet-layout configs like CBS (see
    # _resolve_column_mapping); a no-op lookup for every other, flat-mapping
    # file type.
    for row in kept_rows:
        row_column_mapping = _resolve_column_mapping(config, row.get("worksheet"))
        for field_name in config.required_columns:
            col_ref = str(row_column_mapping.get(field_name, ""))
            if col_ref and row["cells"].get(col_ref) in (None, ""):
                _record_exception(
                    uploaded_file,
                    "MISSING_REQUIRED_FIELD",
                    f"Row missing required field '{field_name}' (column {col_ref})",
                    severity=ValidationSeverity.WARNING,
                    row_reference=f"{row.get('worksheet') or ''}:{row['row_number']}",
                )

    stage3_context = {
        "uploaded_file_id": uploaded_file_id,
        "rows": kept_rows,
        "halted": False,
        "stripped_count": stripped_count,
    }

    if config.date_validation_mode == DateValidationMode.RANGE_CONTAINS:
        # The file's own business-date field is a range (e.g. CBS GL
        # Extract's "From 13/07 To 15/07"), so business_date can't be
        # resolved yet -- pause here, surface the distinct dates actually
        # found, and let a human pick one (see the "Select business date"
        # admin action in file_processing/admin.py). Stage output is
        # persisted to pending_pipeline_context (not just returned) since
        # this pause can last minutes to days -- longer than it's safe to
        # rely on Celery's broker/result-backend transport for (see module
        # docstring's KNOWN SIMPLIFICATION).
        detected_dates = sorted(
            {
                d
                for d in (
                    _row_date_iso(
                        row["cells"],
                        str(_resolve_column_mapping(config, row.get("worksheet")).get("date", "")),
                    )
                    for row in kept_rows
                )
                if d
            }
        )
        uploaded_file.detected_dates = detected_dates
        uploaded_file.pending_pipeline_context = stage3_context
        uploaded_file.processing_status = ProcessingStatus.AWAITING_DATE_SELECTION
        uploaded_file.rejected_record_count = 0
        uploaded_file.save(
            update_fields=[
                "detected_dates",
                "pending_pipeline_context",
                "processing_status",
                "rejected_record_count",
            ]
        )
        _record_exception(
            uploaded_file,
            "AWAITING_DATE_SELECTION",
            f"RANGE_CONTAINS file type; detected distinct row dates {detected_dates} -- "
            "select a business date via the 'Select business date' admin action before "
            "normalization can proceed.",
            severity=ValidationSeverity.INFO,
        )
        return {**stage3_context, "halted": True}

    uploaded_file.processing_status = ProcessingStatus.VALIDATED
    uploaded_file.rejected_record_count = 0  # structural rejects, not row-level; row rejects happen in normalize
    uploaded_file.save(update_fields=["processing_status", "rejected_record_count"])

    return stage3_context


# ---------------------------------------------------------------------------
# Stage 3: normalize_rows
# ---------------------------------------------------------------------------
def _extract_cbs_rrn(remarks: str | None, expected_digits: int) -> tuple[str | None, bool, str | None]:
    """Returns (direction_hint, rrn, rrn_valid) style tuple for CBS remarks.
    See module-level _CBS_REMARKS_RE docstring."""
    if not remarks:
        return None, False, None
    m = _CBS_REMARKS_RE.match(remarks)
    if not m:
        return None, False, None
    prefix, rrn = m.group(1), m.group(2)
    rrn_valid = bool(re.fullmatch(rf"\d{{{expected_digits}}}", rrn))
    direction = Direction.CREDIT if prefix == "By" else Direction.DEBIT
    return direction, rrn, rrn_valid


# Several non-CBS file types (DRC, Timeout, Adjustment -- confirmed directly
# against sample data) carry a leading apostrophe on RRN/STAN/account-number
# fields (a spreadsheet "force text" convention), not present on Raw Data /
# Merchant Raw Data / BBP's headerless TX rows. Stripped unconditionally
# here since a genuine RRN is never expected to start with one.
_LEADING_APOSTROPHE_RE = re.compile(r"^'")


def _extract_generic_rrn(raw_value: Any, expected_digits: int) -> tuple[str | None, bool]:
    """RRN extraction for every file type that isn't CBS (i.e. doesn't use
    the `remarks_prefix` direction_derivation_rule mechanism): the RRN is
    read directly from `column_mapping["rrn"]`'s position -- no
    regex/description parsing needed, unlike CBS's remarks field. No
    direction-derivation rule is confirmed for any of these six types (see
    doubts.md / this module's Stage 4 handoff notes) -- direction is left
    at Direction.UNKNOWN by the caller regardless of this function's
    result."""
    if raw_value in (None, ""):
        return None, False
    cleaned = _LEADING_APOSTROPHE_RE.sub("", str(raw_value)).strip()
    rrn_valid = bool(re.fullmatch(rf"\d{{{expected_digits}}}", cleaned))
    return cleaned, rrn_valid


@shared_task
def normalize_rows(context: dict[str, Any]) -> dict[str, Any]:
    if context.get("halted"):
        return context

    uploaded_file_id = context["uploaded_file_id"]
    uploaded_file = UploadedFile.objects.select_related("file_type_config").get(pk=uploaded_file_id)
    config = uploaded_file.file_type_config
    rows = context["rows"]

    business_date_iso = uploaded_file.business_date.isoformat()
    scale = config.amount_scale_factor or Decimal("1")

    normalized_rows = []
    valid_count = 0
    rejected_count = 0

    for row in rows:
        cells = row["cells"]
        row_ref = f"{row.get('worksheet') or ''}:{row['row_number']}"

        # Resolved per-row's worksheet -- for multi-worksheet, per-worksheet-
        # layout configs like CBS (whose 3 worksheets each use a different
        # column layout for the same continuous report, see
        # _resolve_column_mapping), this genuinely varies row to row; for
        # every other, flat-mapping file type it's the same dict every time.
        row_column_mapping = _resolve_column_mapping(config, row.get("worksheet"))
        date_col = str(row_column_mapping.get("date", ""))
        remarks_col = str(row_column_mapping.get("remarks", ""))
        amount_cols = [str(row_column_mapping.get(f)) for f in config.amount_source_fields]

        # --- business-date row filtering (RANGE_CONTAINS mode only) ---
        row_date_iso = _row_date_iso(cells, date_col)

        if config.date_validation_mode == DateValidationMode.RANGE_CONTAINS:
            if row_date_iso and row_date_iso != business_date_iso:
                # In-scope-file but out-of-scope-date: excluded from the
                # load, not an error (resolved with project owner -- see
                # approved plan's CBS date-range finding).
                continue

        # --- RRN / direction / amount extraction ---
        # CBS-specific mechanism (remarks_prefix rule) is applied when the
        # config names it; every other file type (Raw Data, Merchant Raw
        # Data, Merchant Raw Data BBP, DRC, Timeout, Adjustment) uses the
        # generic position-based extraction below instead.
        direction = Direction.UNKNOWN
        rrn = None
        rrn_valid = False

        if config.direction_derivation_rule.startswith("remarks_prefix"):
            remarks_val = cells.get(remarks_col)
            direction_hint, rrn, rrn_valid = _extract_cbs_rrn(
                remarks_val, config.rrn_expected_digit_length
            )
            if direction_hint is not None:
                direction = direction_hint

            # Cross-check direction against which amount column is
            # populated -- mismatch is a row-level warning, not a rejection
            # (approved plan §4).
            if len(amount_cols) == 2:
                withdrawals_col, deposits_col = amount_cols
                amount_says_debit = cells.get(withdrawals_col) not in (None, "")
                amount_says_credit = cells.get(deposits_col) not in (None, "")
                if (direction == Direction.DEBIT and not amount_says_debit and amount_says_credit) or (
                    direction == Direction.CREDIT and not amount_says_credit and amount_says_debit
                ):
                    _record_exception(
                        uploaded_file,
                        "DIRECTION_AMOUNT_MISMATCH",
                        f"Remarks prefix implies {direction} but populated amount column "
                        f"disagrees (withdrawals={cells.get(withdrawals_col)}, "
                        f"deposits={cells.get(deposits_col)})",
                        severity=ValidationSeverity.WARNING,
                        row_reference=row_ref,
                    )
        else:
            # Generic path: RRN read directly from column_mapping["rrn"]'s
            # position. Direction has no confirmed derivation rule for any
            # of these six types (see doubts.md) -- deliberately left as
            # Direction.UNKNOWN rather than guessed; a future Business Rule
            # Engine / project-owner decision would set this, not this task.
            rrn_col = str(config.column_mapping.get("rrn", ""))
            if rrn_col:
                rrn, rrn_valid = _extract_generic_rrn(
                    cells.get(rrn_col), config.rrn_expected_digit_length
                )

        raw_amount = next((cells.get(c) for c in amount_cols if cells.get(c) not in (None, "")), None)
        amount = None
        if raw_amount is not None:
            try:
                amount = Decimal(str(raw_amount)) * scale
            except Exception:  # noqa: BLE001 - malformed amount is a row-level data issue, not a crash
                _record_exception(
                    uploaded_file,
                    "UNPARSEABLE_AMOUNT",
                    f"Could not parse amount value '{raw_amount}'",
                    severity=ValidationSeverity.WARNING,
                    row_reference=row_ref,
                )

        # RRN invalidity (e.g. CBS's ~10% non-12-digit rate, confirmed
        # directly against sample data) is a normal per-row state, not an
        # error path -- the row is still normalized/kept, just excluded
        # from RRN-keyed matching downstream (approved plan finding).
        normalized_rows.append(
            {
                "row_number": row["row_number"],
                "worksheet": row.get("worksheet"),
                "cells": cells,
                "rrn": rrn,
                "rrn_valid": rrn_valid,
                "direction": direction,
                "amount": str(amount) if amount is not None else None,
                "row_date": row_date_iso,
            }
        )
        valid_count += 1

    uploaded_file.valid_record_count = valid_count
    uploaded_file.rejected_record_count = rejected_count
    uploaded_file.processing_status = ProcessingStatus.NORMALIZED
    uploaded_file.save(update_fields=["valid_record_count", "rejected_record_count", "processing_status"])

    return {
        "uploaded_file_id": uploaded_file_id,
        "rows": normalized_rows,
        "halted": False,
    }


# ---------------------------------------------------------------------------
# Stage 4: load_to_unified_table
# ---------------------------------------------------------------------------
@shared_task
def load_to_unified_table(context: dict[str, Any]) -> dict[str, Any]:
    """
    Populates `transactions.TransactionRecord` from `normalize_rows`'
    output. Scope: the six RRN-bearing file types resolved 2026-09-10 (CBS
    GL Extract, Raw Data, Merchant Raw Data, Merchant Raw Data BBP, DRC,
    Timeout, Adjustment -- 7 `FileTypeConfig`s across those 6 underlying
    types). NTSL is out of scope entirely and must never reach this task
    (see doubts.md's NTSL entry) -- it is deliberately not wired into any
    `FileTypeConfig` this stage would run against.

    business_date resolution per row: uses the row's own resolved date
    (`row_date`, from `normalize_rows`' `_row_date_iso` call against
    `column_mapping["date"]`) when available, falling back to
    `uploaded_file.business_date` otherwise. This matters specifically for
    Adjustment, whose `Adjdate` can genuinely differ from the file's
    nominal business date (confirmed: one row in the real sample has
    Adjdate 2026-07-15 in a file otherwise dated 2026-07-14) -- see
    doubts.md's "Adjustment report" resolution. For every other in-scope
    type the row date is either unmapped (Raw Data/Merchant Raw Data/BBP,
    whose business_date is a whole-file constant entered at upload) or
    confirmed constant across the file (DRC/Timeout), so this fallback is
    inert for them in practice.

    A row whose amount could not be parsed (`normalize_rows` already logged
    an UNPARSEABLE_AMOUNT warning and kept the row) cannot be loaded here --
    `TransactionRecord.amount` is a required, non-null column -- so it is
    skipped with its own logged exception rather than silently coerced to
    e.g. zero, which would fabricate a value never present in the source
    file.

    `direction` is carried over as whatever `normalize_rows` resolved:
    CBS's remarks-prefix rule, or `Direction.UNKNOWN` for the other five
    types (see `_extract_generic_rrn`'s docstring -- no direction-derivation
    rule is confirmed for them; this is a Business Rule Engine / project
    owner decision, not invented here).

    Uses `bulk_create(..., ignore_conflicts=True)` against
    `unique_row_per_uploaded_file` so a retried Celery task (the scenario
    that constraint was added for) doesn't raise or double-load rows.
    """
    if context.get("halted"):
        return context

    uploaded_file_id = context["uploaded_file_id"]
    uploaded_file = UploadedFile.objects.select_related(
        "file_type_config", "payment_module", "settlement_cycle"
    ).get(pk=uploaded_file_id)
    config = uploaded_file.file_type_config
    rows = context["rows"]

    records: list[TransactionRecord] = []
    loaded_count = 0
    skipped_count = 0

    for row in rows:
        row_ref = f"{row.get('worksheet') or ''}:{row['row_number']}"

        if row.get("amount") is None:
            _record_exception(
                uploaded_file,
                "SKIPPED_UNLOADABLE_ROW",
                "Row has no parseable amount (see prior UNPARSEABLE_AMOUNT warning, if "
                "any) -- TransactionRecord.amount is required, so this row cannot be "
                "loaded and is excluded rather than assigned a fabricated value.",
                row_reference=row_ref,
            )
            skipped_count += 1
            continue

        row_date_iso = row.get("row_date")
        business_date = date.fromisoformat(row_date_iso) if row_date_iso else uploaded_file.business_date

        records.append(
            TransactionRecord(
                payment_module=uploaded_file.payment_module,
                source_register_type=config.register_type,
                uploaded_file=uploaded_file,
                settlement_cycle=uploaded_file.settlement_cycle,
                business_date=business_date,
                rrn=row.get("rrn") or "",
                rrn_valid=bool(row.get("rrn_valid")),
                amount=Decimal(row["amount"]),
                direction=row.get("direction") or Direction.UNKNOWN,
                row_reference=row_ref,
                extra_fields={"raw_cells": row["cells"]},
            )
        )
        loaded_count += 1

    if records:
        TransactionRecord.objects.bulk_create(records, ignore_conflicts=True)

    uploaded_file.processing_status = ProcessingStatus.LOADED
    uploaded_file.save(update_fields=["processing_status"])

    return {
        "uploaded_file_id": uploaded_file_id,
        "loaded_count": loaded_count,
        "skipped_count": skipped_count,
        "halted": False,
    }
