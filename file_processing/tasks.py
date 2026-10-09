"""
Celery pipeline for inbound source-file processing (approved plan §5),
restructured for the staging -> fact -> history schema redesign (approved
2026-09-23, see tracker.md "Schema Redesign -- Staging -> Fact -> History").

Stages, in order:
  1 `format_sniff_and_parse`
  2 `validate_structure_and_content`
  3 `load_to_staging` -- writes every surviving parsed row, in raw form, to
    its per-file-type staging table (`transactions.models.staging`, chosen
    via `STAGING_MODEL_BY_REGISTER_TYPE`). Status -> STAGED.
  4 `normalize_rows` -- reads the rows back from that staging table
    (filtered by uploaded_file_id), same RRN/direction/amount/date
    extraction logic as before, carrying each staging row's id forward.
  5 `promote_to_fact` (formerly `load_to_unified_table`) -- builds
    `transactions.TransactionFact` rows with `staging_row_id` lineage.
    Status -> LOADED.
In scope for stages 4-5: CBS GL Extract, Raw Data, Merchant Raw Data
(ACQ/ISS), Merchant Raw Data BBP, DRC, Timeout, Adjustment.

NTSL (added 2026-09-27) is a settlement summary/totals report with no RRN
column (see doubts.md), so it never reaches normalize_rows/promote_to_fact.
It runs stages 1-3 unchanged (staged into `StagingNtsl`, every parsed row
kept) and then its own final stage instead:
  4' `promote_to_settlement_summary` -- promotes the genuine category lines
    to `transactions.NtslSettlementLine`. Status -> LOADED.

Each stage is a separate Celery task chained together (`build_processing_
chain`) so it's independently retryable/visible, rather than one large
task, matching the approved plan.

KNOWN SIMPLIFICATION (narrowed by the staging redesign): a durable,
queryable staging table now exists, so the CBS date-selection pause
(RANGE_CONTAINS) no longer persists the whole parsed file in
`UploadedFile.pending_pipeline_context` -- rows are staged first and the
paused context is just `{"uploaded_file_id": ..., "halted": True}`. Rows
still travel between stages 1 -> 2 -> 3 (parse -> validate -> stage) and
4 -> 5 (normalized values, without raw cells) as each Celery task's
JSON-serializable return value, i.e. through the broker/result backend,
for the duration of one uninterrupted chain run. Row dict cell keys are
stringified (`{"1": ..., "3": ...}`) since JSON object keys are always
strings; staging's `raw_cells` keeps that same string-keyed form.
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

from core.models import Direction, RegisterType
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
from transactions.models import (
    STAGING_MODEL_BY_REGISTER_TYPE,
    NtslSettlementLine,
    StagingNtsl,
    TransactionFact,
)

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

    For EXACT_MATCH file types this is the full 5-stage chain. For
    RANGE_CONTAINS file types (e.g. CBS GL Extract) the chain stops after
    load_to_staging: rows are staged first, then that stage pauses
    processing at AWAITING_DATE_SELECTION with the file's distinct row
    dates surfaced. normalize_rows + promote_to_fact are invoked later as
    their own standalone chain -- from the "Select business date" admin
    action, see file_processing/admin.py -- once a human has picked one;
    normalize_rows reads the already-staged rows back, so nothing but the
    uploaded_file_id needs to survive the pause.

    NTSL files (register_type NTSL) get their own 4-stage chain ending in
    promote_to_settlement_summary instead of normalize_rows/promote_to_fact
    (settlement summary, no RRN -- see module docstring)."""
    config = (
        UploadedFile.objects.select_related("file_type_config")
        .get(pk=uploaded_file_id)
        .file_type_config
    )
    if config.register_type == RegisterType.NTSL:
        return chain(
            format_sniff_and_parse.s(uploaded_file_id),
            validate_structure_and_content.s(),
            load_to_staging.s(),
            promote_to_settlement_summary.s(),
        )
    date_validation_mode = config.date_validation_mode
    if date_validation_mode == DateValidationMode.RANGE_CONTAINS:
        return chain(
            format_sniff_and_parse.s(uploaded_file_id),
            validate_structure_and_content.s(),
            load_to_staging.s(),
        )
    return chain(
        format_sniff_and_parse.s(uploaded_file_id),
        validate_structure_and_content.s(),
        load_to_staging.s(),
        normalize_rows.s(),
        promote_to_fact.s(),
    )


def _row_reference(worksheet: str | None, row_number: int) -> str:
    """'<worksheet>:<row_number>' natural key -- the same format used by
    FileValidationException.row_reference, staging row_reference, and
    TransactionFact.row_reference."""
    return f"{worksheet or ''}:{row_number}"


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

    update_fields = ["processing_status", "rejected_record_count"]
    if config.date_validation_mode == DateValidationMode.RANGE_CONTAINS:
        # The file's own business-date field is a range (e.g. CBS GL
        # Extract's "From 13/07 To 15/07"), so business_date can't be
        # resolved yet. The distinct dates actually found are computed and
        # stored here; the pause itself (AWAITING_DATE_SELECTION) now
        # happens at the end of load_to_staging, AFTER the rows are durably
        # staged, so the paused context no longer has to carry them (see
        # module docstring's KNOWN SIMPLIFICATION).
        uploaded_file.detected_dates = sorted(
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
        update_fields.append("detected_dates")

    uploaded_file.processing_status = ProcessingStatus.VALIDATED
    uploaded_file.rejected_record_count = 0  # structural rejects, not row-level; row rejects happen in normalize
    uploaded_file.save(update_fields=update_fields)

    return stage3_context


# ---------------------------------------------------------------------------
# Stage 3: load_to_staging
# ---------------------------------------------------------------------------
_STAGING_BATCH_SIZE = 5000


def _staging_model_or_fail(uploaded_file: UploadedFile, config, stage_name: str):
    """Returns the staging model for this file's register_type, or records
    an ERROR exception, marks the file FAILED, and returns None -- e.g. for
    SWITCH, which has no staging table by design (no Switch file exists)."""
    staging_model = STAGING_MODEL_BY_REGISTER_TYPE.get(config.register_type)
    if staging_model is None:
        _record_exception(
            uploaded_file,
            "NO_STAGING_TABLE",
            f"{stage_name}: register_type '{config.register_type}' has no staging table "
            "(in scope: CBS GL Extract, Raw Data, Merchant Raw Data, Merchant Raw Data BBP, "
            "DRC, Timeout, Adjustment, NTSL)",
        )
        uploaded_file.processing_status = ProcessingStatus.FAILED
        uploaded_file.save(update_fields=["processing_status"])
    return staging_model


@shared_task
def load_to_staging(context: dict[str, Any]) -> dict[str, Any]:
    """
    Writes every row that survived validate_structure_and_content, in raw
    form, to the per-file-type staging table for this file's register_type
    (`transactions.models.staging`). Nothing is normalized here.

    Each row stores its full parsed cell dict in `raw_cells`, plus the
    model's typed raw columns, filled from the file type's EXISTING
    `FileTypeConfig.column_mapping` through `_resolve_column_mapping` (so
    CBS's per-worksheet layouts resolve correctly). A typed column whose
    mapping key isn't in the config is left NULL -- its value is still in
    `raw_cells`. No column position is hardcoded here.

    RANGE_CONTAINS files (CBS) stage ALL rows, across every date in the
    file's range; date filtering happens later, in normalize_rows, once a
    business date has been selected. After staging, such a file pauses at
    AWAITING_DATE_SELECTION with only {"uploaded_file_id", "halted": True}
    persisted in `pending_pipeline_context`.

    Idempotent: `bulk_create(ignore_conflicts=True)` against each staging
    table's (uploaded_file, row_reference) unique constraint, so a retried
    task doesn't double-stage rows.
    """
    if context.get("halted"):
        return context

    uploaded_file_id = context["uploaded_file_id"]
    uploaded_file = UploadedFile.objects.select_related("file_type_config").get(pk=uploaded_file_id)
    config = uploaded_file.file_type_config

    staging_model = _staging_model_or_fail(uploaded_file, config, "load_to_staging")
    if staging_model is None:
        return {"uploaded_file_id": uploaded_file_id, "halted": True}

    has_worksheet_column = any(f.name == "worksheet" for f in staging_model._meta.fields)
    typed_field_keys = staging_model.TYPED_FIELD_MAPPING_KEYS

    staging_rows = []
    for row in context.get("rows", []):
        worksheet = row.get("worksheet")
        cells = row["cells"]
        row_column_mapping = _resolve_column_mapping(config, worksheet)

        typed_values: dict[str, Any] = {}
        for field_name, mapping_key in typed_field_keys.items():
            col_ref = row_column_mapping.get(mapping_key)
            if col_ref in (None, ""):
                typed_values[field_name] = None
                continue
            value = cells.get(str(col_ref))
            typed_values[field_name] = None if value is None else str(value)
        if has_worksheet_column:
            typed_values["worksheet"] = worksheet

        staging_rows.append(
            staging_model(
                uploaded_file=uploaded_file,
                row_number=row["row_number"],
                row_reference=_row_reference(worksheet, row["row_number"]),
                raw_cells=cells,
                **typed_values,
            )
        )

    if staging_rows:
        staging_model.objects.bulk_create(
            staging_rows, ignore_conflicts=True, batch_size=_STAGING_BATCH_SIZE
        )
    staged_count = staging_model.objects.filter(uploaded_file=uploaded_file).count()

    if config.date_validation_mode == DateValidationMode.RANGE_CONTAINS:
        paused_context = {"uploaded_file_id": uploaded_file_id, "halted": True}
        uploaded_file.pending_pipeline_context = paused_context
        uploaded_file.processing_status = ProcessingStatus.AWAITING_DATE_SELECTION
        uploaded_file.save(update_fields=["pending_pipeline_context", "processing_status"])
        _record_exception(
            uploaded_file,
            "AWAITING_DATE_SELECTION",
            f"RANGE_CONTAINS file type; {staged_count} rows staged; detected distinct row dates "
            f"{uploaded_file.detected_dates} -- select a business date via the 'Select business "
            "date' admin action before normalization can proceed.",
            severity=ValidationSeverity.INFO,
        )
        return {**paused_context, "staged_count": staged_count}

    uploaded_file.processing_status = ProcessingStatus.STAGED
    uploaded_file.save(update_fields=["processing_status"])
    return {"uploaded_file_id": uploaded_file_id, "halted": False, "staged_count": staged_count}


# ---------------------------------------------------------------------------
# Stage 4: normalize_rows
# ---------------------------------------------------------------------------
# CBS writes RRNs into its remarks as numbers, dropping leading zeros
# (`By UPI/4383934640/...` is RRN 004383934640). Profiled 2026-09-30: every
# short CBS RRN is all digits and 8-11 long, and padded back to 12 they match
# NPCI for 6,803 of 6,805 rows (14 Jul) and 7,201 of 7,253 (26 Sep). Padded
# here, at extraction, at the project owner's request (2026-10-02); the raw
# remarks stay unchanged in staging.
_CBS_MIN_UNPADDED_RRN_DIGITS = 8


def _extract_cbs_rrn(remarks: str | None, expected_digits: int) -> tuple[str | None, bool, str | None]:
    """Returns (direction_hint, rrn, rrn_valid) style tuple for CBS remarks.
    See module-level _CBS_REMARKS_RE docstring."""
    if not remarks:
        return None, False, None
    m = _CBS_REMARKS_RE.match(remarks)
    if not m:
        return None, False, None
    prefix, rrn = m.group(1), m.group(2)
    if rrn.isdigit() and _CBS_MIN_UNPADDED_RRN_DIGITS <= len(rrn) < expected_digits:
        rrn = rrn.zfill(expected_digits)
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
    regex/description parsing needed, unlike CBS's remarks field. Used by
    the `constant:` direction path (Raw Data ACQ/ISS, Merchant Raw Data
    ACQ/ISS, Merchant Raw Data BBP resolved 2026-09-12, see doubts.md), the
    `ssk_party:` path (DRC, Timeout and Adjustment -- `constant:` from
    2026-09-16 until superseded 2026-10-08) -- and, if a
    future file type is added with no `direction_derivation_rule` set at
    all, the generic fallback path that leaves direction at
    Direction.UNKNOWN."""
    if raw_value in (None, ""):
        return None, False
    cleaned = _LEADING_APOSTROPHE_RE.sub("", str(raw_value)).strip()
    rrn_valid = bool(re.fullmatch(rf"\d{{{expected_digits}}}", cleaned))
    return cleaned, rrn_valid


# `ssk_party:` direction rule -- per-row direction from which party column
# names this bank (added 2026-10-08 for DRC and Timeout, whose files carry
# rows from both sides; extended the same day to Adjustment with the
# mapping mirrored; see seed_file_type_configs.py). Format:
#   ssk_party:remitter_col=<n>,beneficiary_col=<n>,bank_code=<code>
#             [,remitter=<DEBIT|CREDIT>,beneficiary=<DEBIT|CREDIT>]
# (1-based column keys, same as FileTypeConfig.column_mapping). Per row:
# remitter cell == bank_code -> the `remitter` direction; else beneficiary
# cell == bank_code -> the `beneficiary` direction; else UNKNOWN, logged as
# a DIRECTION_UNRESOLVED warning. Defaults remitter=DEBIT (this bank's
# customer paid), beneficiary=CREDIT (this bank's customer received) -- the
# DRC/Timeout mapping, so their rule strings carry no mapping keys.
# Adjustment sets remitter=CREDIT,beneficiary=DEBIT: an adjustment moves
# money back to the payer, away from the payee.
SSK_PARTY_RULE_PREFIX = "ssk_party:"
_SSK_PARTY_REQUIRED_KEYS = ("remitter_col", "beneficiary_col", "bank_code")
_SSK_PARTY_MAPPING_DEFAULTS = {"remitter": Direction.DEBIT, "beneficiary": Direction.CREDIT}
_SSK_PARTY_MAPPING_VALUES = (Direction.DEBIT, Direction.CREDIT)


def _parse_ssk_party_rule(rule: str) -> dict[str, str]:
    """Parses an `ssk_party:` rule into {remitter_col, beneficiary_col,
    bank_code, remitter, beneficiary} (the last two defaulted). A malformed
    rule is a configuration error and raises ValueError rather than
    silently loading every row as UNKNOWN."""
    if not rule.startswith(SSK_PARTY_RULE_PREFIX):
        raise ValueError(f"Not an ssk_party rule: {rule!r}")
    params: dict[str, str] = {}
    for part in rule[len(SSK_PARTY_RULE_PREFIX):].split(","):
        key, sep, value = part.partition("=")
        if not sep or not key.strip() or not value.strip():
            raise ValueError(f"Malformed ssk_party rule segment {part!r} in {rule!r}")
        params[key.strip()] = value.strip()
    allowed = (*_SSK_PARTY_REQUIRED_KEYS, *_SSK_PARTY_MAPPING_DEFAULTS)
    missing = [k for k in _SSK_PARTY_REQUIRED_KEYS if k not in params]
    unexpected = [k for k in params if k not in allowed]
    if missing or unexpected:
        raise ValueError(f"ssk_party rule {rule!r}: missing {missing}, unexpected {unexpected}")
    for side, default in _SSK_PARTY_MAPPING_DEFAULTS.items():
        params[side] = params.get(side, default).upper()
        if params[side] not in _SSK_PARTY_MAPPING_VALUES:
            raise ValueError(f"ssk_party rule {rule!r}: {side} must be DEBIT or CREDIT")
    if params["remitter"] == params["beneficiary"]:
        # Same direction for both sides is a constant: rule, not a party rule.
        raise ValueError(f"ssk_party rule {rule!r}: remitter and beneficiary map to the same direction")
    return params


def _party_cell(value: Any) -> str:
    """Party cell text with whitespace and the leading "force text"
    apostrophe removed -- same cleaning as
    reconciliation.presence._clean_cell, so ssk_side and direction agree on
    what "SSK" means."""
    return _LEADING_APOSTROPHE_RE.sub("", str(value or "").strip()).strip()


def _direction_from_ssk_party(cells: dict[str, Any], params: dict[str, str]) -> str:
    """DEBIT / CREDIT / UNKNOWN for one row under a parsed `ssk_party:`
    rule. The remitter check runs first, so an on-us row (bank_code on both
    sides) takes the remitter direction."""
    bank_code = params["bank_code"]
    if _party_cell(cells.get(params["remitter_col"])) == bank_code:
        return params["remitter"]
    if _party_cell(cells.get(params["beneficiary_col"])) == bank_code:
        return params["beneficiary"]
    return Direction.UNKNOWN


@shared_task
def normalize_rows(context: dict[str, Any]) -> dict[str, Any]:
    if context.get("halted"):
        return context

    uploaded_file_id = context["uploaded_file_id"]
    uploaded_file = UploadedFile.objects.select_related("file_type_config").get(pk=uploaded_file_id)
    config = uploaded_file.file_type_config

    # Rows are read back from this file's staging table (written by
    # load_to_staging), not from the incoming context -- so this stage works
    # identically whether it runs straight after staging or is resumed
    # after a CBS date-selection pause.
    staging_model = _staging_model_or_fail(uploaded_file, config, "normalize_rows")
    if staging_model is None:
        return {"uploaded_file_id": uploaded_file_id, "rows": [], "halted": True}
    staged_rows = staging_model.objects.filter(uploaded_file_id=uploaded_file_id).order_by("id")

    business_date_iso = uploaded_file.business_date.isoformat()
    scale = config.amount_scale_factor or Decimal("1")

    normalized_rows = []
    valid_count = 0
    rejected_count = 0
    direction_unresolved_count = 0
    # Parsed once per file (raises on a malformed rule -- a config error).
    ssk_party_params = (
        _parse_ssk_party_rule(config.direction_derivation_rule)
        if config.direction_derivation_rule.startswith(SSK_PARTY_RULE_PREFIX)
        else None
    )

    for staging_row in staged_rows.iterator(chunk_size=_STAGING_BATCH_SIZE):
        cells = staging_row.raw_cells
        # Only the CBS staging table has a worksheet column; every other
        # in-scope type is single-sheet CSV (worksheet None), same as before.
        row = {"row_number": staging_row.row_number, "worksheet": getattr(staging_row, "worksheet", None)}
        row_ref = staging_row.row_reference

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

        if config.direction_derivation_rule.startswith("constant:"):
            # Whole-file-constant direction, keyed purely by which
            # FileTypeConfig (i.e. which source file -- ACQ vs. ISS, or
            # BBP's ACQ-only variant) this row came from, not any per-row
            # column. Confirmed via the bank's SOP (see doubts.md's
            # "Resolved" section, 2026-09-12): "Issuer transactions are
            # credit transaction to the bank customer... Acquirer
            # transactions are debit transaction to the bank customers."
            rrn_col = str(config.column_mapping.get("rrn", ""))
            if rrn_col:
                rrn, rrn_valid = _extract_generic_rrn(
                    cells.get(rrn_col), config.rrn_expected_digit_length
                )
            direction = config.direction_derivation_rule.split(":", 1)[1].strip()
        elif ssk_party_params is not None:
            # Per-row direction from the remitter/beneficiary party columns
            # (DRC, Timeout and -- mirrored -- Adjustment since 2026-10-08;
            # see _direction_from_ssk_party
            # and seed_file_type_configs.py for why the constant rule was
            # superseded).
            rrn_col = str(config.column_mapping.get("rrn", ""))
            if rrn_col:
                rrn, rrn_valid = _extract_generic_rrn(
                    cells.get(rrn_col), config.rrn_expected_digit_length
                )
            direction = _direction_from_ssk_party(cells, ssk_party_params)
            if direction == Direction.UNKNOWN:
                direction_unresolved_count += 1
                _record_exception(
                    uploaded_file,
                    "DIRECTION_UNRESOLVED",
                    f"Neither Remitter (col {ssk_party_params['remitter_col']}="
                    f"{_party_cell(cells.get(ssk_party_params['remitter_col']))!r}) nor Beneficiary "
                    f"(col {ssk_party_params['beneficiary_col']}="
                    f"{_party_cell(cells.get(ssk_party_params['beneficiary_col']))!r}) is "
                    f"{ssk_party_params['bank_code']}; direction left UNKNOWN",
                    severity=ValidationSeverity.WARNING,
                    row_reference=row_ref,
                )
        elif config.direction_derivation_rule.startswith("remarks_prefix"):
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
            # Generic fallback path: RRN read directly from
            # column_mapping["rrn"]'s position, direction left at
            # Direction.UNKNOWN. Reached only if a config has no
            # `direction_derivation_rule` set at all -- every one of the
            # six in-scope non-CBS file types now sets `constant:` (Raw
            # Data ACQ/ISS, Merchant Raw Data ACQ/ISS, Merchant Raw Data
            # BBP resolved 2026-09-12, see doubts.md) or `ssk_party:` (DRC,
            # Timeout, Adjustment -- constant from 2026-09-16, per-row since
            # 2026-10-08), so this branch is currently a
            # defensive fallback for a not-yet-configured file type, not a
            # live path.
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

        # Adjustment only: the original disputed transaction's Txnamount
        # (-> TransactionFact.txn_amount). Read from the staging row's typed
        # txn_amount_raw column, which is only populated when the ADJUSTMENT
        # config's column_mapping has a "txn_amount" key -- see
        # transactions/models/staging.py AdjustmentRawFields. `amount`
        # above is unaffected (still Adjamount).
        txn_amount = None
        raw_txn_amount = getattr(staging_row, "txn_amount_raw", None)
        if raw_txn_amount not in (None, ""):
            try:
                txn_amount = Decimal(str(raw_txn_amount)) * scale
            except Exception:  # noqa: BLE001 - row-level data issue, not a crash
                _record_exception(
                    uploaded_file,
                    "UNPARSEABLE_TXN_AMOUNT",
                    f"Could not parse Txnamount value '{raw_txn_amount}'",
                    severity=ValidationSeverity.WARNING,
                    row_reference=row_ref,
                )

        # RRN invalidity (e.g. CBS's ~10% non-12-digit rate, confirmed
        # directly against sample data) is a normal per-row state, not an
        # error path -- the row is still normalized/kept, just excluded
        # from RRN-keyed matching downstream (approved plan finding).
        # Raw cells are no longer forwarded: TransactionFact has no
        # extra_fields, and the raw row stays reachable via staging_row_id.
        normalized_rows.append(
            {
                "row_number": row["row_number"],
                "worksheet": row.get("worksheet"),
                "row_reference": row_ref,
                "staging_row_id": staging_row.id,
                "rrn": rrn,
                "rrn_valid": rrn_valid,
                "direction": direction,
                "amount": str(amount) if amount is not None else None,
                "txn_amount": str(txn_amount) if txn_amount is not None else None,
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
        # Rows an `ssk_party:` rule could not resolve (0 for other rules).
        "direction_unresolved_count": direction_unresolved_count,
    }


# ---------------------------------------------------------------------------
# Stage 5: promote_to_fact (formerly load_to_unified_table)
# ---------------------------------------------------------------------------
@shared_task
def promote_to_fact(context: dict[str, Any]) -> dict[str, Any]:
    """
    Builds `transactions.TransactionFact` rows from `normalize_rows`'
    output. Renamed from `load_to_unified_table` by the 2026-09-23 schema
    redesign; the per-row logic below is unchanged apart from the target
    model, `staging_row_id` lineage, `txn_amount`, and `extra_fields` being
    gone. Scope: the six RRN-bearing file types (CBS GL Extract, Raw Data,
    Merchant Raw Data, Merchant Raw Data BBP, DRC, Timeout, Adjustment).
    NTSL never reaches this stage -- it ends in promote_to_settlement_summary.

    business_date resolution per row: uses the row's own resolved date
    (`row_date`, from `normalize_rows`' `_row_date_iso` call against
    `column_mapping["date"]`) when available, falling back to
    `uploaded_file.business_date` otherwise. This matters specifically for
    Adjustment, whose `Adjdate` can genuinely differ from the file's
    nominal business date (confirmed: one row in the real sample has
    Adjdate 2026-07-15 in a file otherwise dated 2026-07-14) -- see
    doubts.md's "Adjustment report" resolution.

    `amount` is unchanged (for Adjustment it is still Adjamount). `txn_amount`
    is set ONLY for Adjustment rows, from normalize_rows' `txn_amount`
    (Txnamount); NULL for every other register type.

    A row whose amount could not be parsed (`normalize_rows` already logged
    an UNPARSEABLE_AMOUNT warning and kept the row) cannot be loaded here --
    `TransactionFact.amount` is a required, non-null column -- so it is
    skipped with its own logged exception rather than silently coerced to
    e.g. zero, which would fabricate a value never present in the source
    file.

    `direction` is carried over as whatever `normalize_rows` resolved: CBS's
    `remarks_prefix` rule, a whole-file `constant:DEBIT`/`constant:CREDIT`
    rule, a per-row `ssk_party:` rule (DRC/Timeout; UNKNOWN when neither
    party is this bank), or `Direction.UNKNOWN` for any type with no
    `direction_derivation_rule` set at all.

    Uses `bulk_create(..., ignore_conflicts=True)` against
    `unique_row_per_uploaded_file` so a retried Celery task doesn't raise or
    double-load rows.
    """
    if context.get("halted"):
        return context

    uploaded_file_id = context["uploaded_file_id"]
    uploaded_file = UploadedFile.objects.select_related(
        "file_type_config", "payment_module", "settlement_cycle"
    ).get(pk=uploaded_file_id)
    config = uploaded_file.file_type_config
    rows = context["rows"]
    is_adjustment = config.register_type == RegisterType.ADJUSTMENT

    facts: list[TransactionFact] = []
    loaded_count = 0
    skipped_count = 0

    for row in rows:
        row_ref = row.get("row_reference") or _row_reference(row.get("worksheet"), row["row_number"])

        if row.get("amount") is None:
            _record_exception(
                uploaded_file,
                "SKIPPED_UNLOADABLE_ROW",
                "Row has no parseable amount (see prior UNPARSEABLE_AMOUNT warning, if "
                "any) -- TransactionFact.amount is required, so this row cannot be "
                "loaded and is excluded rather than assigned a fabricated value.",
                row_reference=row_ref,
            )
            skipped_count += 1
            continue

        row_date_iso = row.get("row_date")
        business_date = date.fromisoformat(row_date_iso) if row_date_iso else uploaded_file.business_date

        txn_amount = None
        if is_adjustment and row.get("txn_amount") is not None:
            txn_amount = Decimal(row["txn_amount"])

        facts.append(
            TransactionFact(
                payment_module=uploaded_file.payment_module,
                source_register_type=config.register_type,
                uploaded_file=uploaded_file,
                settlement_cycle=uploaded_file.settlement_cycle,
                business_date=business_date,
                rrn=row.get("rrn") or "",
                rrn_valid=bool(row.get("rrn_valid")),
                amount=Decimal(row["amount"]),
                txn_amount=txn_amount,
                direction=row.get("direction") or Direction.UNKNOWN,
                row_reference=row_ref,
                staging_row_id=row.get("staging_row_id"),
            )
        )
        loaded_count += 1

    if facts:
        TransactionFact.objects.bulk_create(facts, ignore_conflicts=True, batch_size=_STAGING_BATCH_SIZE)

    uploaded_file.processing_status = ProcessingStatus.LOADED
    uploaded_file.save(update_fields=["processing_status"])

    return {
        "uploaded_file_id": uploaded_file_id,
        "loaded_count": loaded_count,
        "skipped_count": skipped_count,
        "halted": False,
    }


# ---------------------------------------------------------------------------
# NTSL final stage: promote_to_settlement_summary (replaces stages 4-5 for NTSL)
# ---------------------------------------------------------------------------
# Row 3 of every real NTSL file (all 12 in 14072026/NTSL/ checked), e.g.
# "Daily Settlement Statement for SUCO SOUHARDA SAHAKARI BANK LTD - UPI as on
# 14-07-2026 ( 1C 21:00:00 TO 00:00:00 )" / "... ( DC1 00:00:00 TO 16:00:00 )".
_NTSL_TITLE_RE = re.compile(r"as on (\d{2}-\d{2}-\d{4}) \( (\S+) ")
_NTSL_FINAL_SETTLEMENT_DESCRIPTION = "final settlement amount"


def _parse_ntsl_number(raw: Any) -> Decimal | None:
    """Decimal for a numeric NTSL cell ("2257.0", "0", "-12.5"); None for
    None/blank/whitespace or anything that isn't a finite number (e.g. the
    header row's "No of Txns"/"Debit"/"Credit" labels)."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        value = Decimal(text)
    except Exception:  # noqa: BLE001 - decimal.InvalidOperation etc.: not a number
        return None
    return value if value.is_finite() else None


def _is_blank(raw: Any) -> bool:
    return raw is None or not str(raw).strip()


@shared_task
def promote_to_settlement_summary(context: dict[str, Any]) -> dict[str, Any]:
    """
    NTSL's final stage (after load_to_staging): promotes the genuine
    category lines of one NTSL file from `StagingNtsl` to
    `transactions.NtslSettlementLine`. Loading only -- NTSL validation /
    settlement reconciliation is Phase 4 work and is not done here.

    A staged row is a category line when its description (stripped) is
    non-empty AND at least one of txn_count/debit/credit parses as a
    number. That excludes the title rows, the "Description | No of Txns |
    Debit | Credit" header (its number cells are text labels), blank rows,
    and heading rows such as "FOR NON - FINANCIAL MANDATE/OD TXNs" (blank/
    whitespace number cells). Rows with a parseable number but an empty
    description are not loaded; one INFO exception records how many.

    Per line: txn_count "18.0" -> 18, blank -> NULL, non-integral or
    non-numeric -> NULL plus a WARNING (never guessed). debit/credit ->
    Decimal, blank/whitespace -> 0. A non-blank debit/credit that does not
    parse would have to be invented to load, so that line is skipped with a
    WARNING instead (does not occur in any of the 12 real files).
    is_final_settlement = description == "Final Settlement Amount"
    (case-insensitive).

    Non-fatal WARNING cross-checks (the file still reaches LOADED): the
    title row's "as on <date> ( <cycle> " must match the UploadedFile's
    business_date / settlement_cycle (WARNING if it differs or no such
    title row exists), and exactly one final-settlement line must exist.

    Idempotent: bulk_create(ignore_conflicts=True) against
    unique_ntsl_line_per_uploaded_file. (A re-run does re-record its
    INFO/WARNING exceptions, same as the other stages.)
    """
    if context.get("halted"):
        return context

    uploaded_file_id = context["uploaded_file_id"]
    uploaded_file = UploadedFile.objects.select_related(
        "file_type_config", "payment_module", "settlement_cycle"
    ).get(pk=uploaded_file_id)
    config = uploaded_file.file_type_config

    if config.register_type != RegisterType.NTSL:
        _record_exception(
            uploaded_file,
            "NOT_NTSL",
            f"promote_to_settlement_summary: register_type '{config.register_type}' is not NTSL",
        )
        uploaded_file.processing_status = ProcessingStatus.FAILED
        uploaded_file.save(update_fields=["processing_status"])
        return {"uploaded_file_id": uploaded_file_id, "halted": True}

    staged_rows = StagingNtsl.objects.filter(uploaded_file_id=uploaded_file_id).order_by("row_number", "id")

    lines: list[NtslSettlementLine] = []
    title_match = None
    empty_description_refs: list[str] = []

    for staging_row in staged_rows:
        description = (staging_row.description_raw or "").strip()
        if title_match is None and description:
            title_match = _NTSL_TITLE_RE.search(description)

        txn_count_raw = staging_row.txn_count_raw
        debit_raw = staging_row.debit_raw
        credit_raw = staging_row.credit_raw
        txn_count_num = _parse_ntsl_number(txn_count_raw)
        debit_num = _parse_ntsl_number(debit_raw)
        credit_num = _parse_ntsl_number(credit_raw)
        if txn_count_num is None and debit_num is None and credit_num is None:
            continue  # title / header / blank / heading row
        if not description:
            empty_description_refs.append(staging_row.row_reference)
            continue

        row_ref = staging_row.row_reference

        bad_amounts = [
            (label, raw)
            for label, raw, num in (("Debit", debit_raw, debit_num), ("Credit", credit_raw, credit_num))
            if num is None and not _is_blank(raw)
        ]
        if bad_amounts:
            _record_exception(
                uploaded_file,
                "NTSL_UNPARSEABLE_AMOUNT",
                f"NTSL line '{description}' not loaded: non-numeric "
                + ", ".join(f"{label}={raw!r}" for label, raw in bad_amounts),
                severity=ValidationSeverity.WARNING,
                row_reference=row_ref,
            )
            continue

        txn_count = None
        if txn_count_num is not None:
            if txn_count_num == txn_count_num.to_integral_value():
                txn_count = int(txn_count_num)
            else:
                _record_exception(
                    uploaded_file,
                    "NTSL_NON_INTEGRAL_TXN_COUNT",
                    f"NTSL line '{description}': No of Txns {txn_count_raw!r} is not a whole "
                    "number -- stored as NULL rather than rounded.",
                    severity=ValidationSeverity.WARNING,
                    row_reference=row_ref,
                )
        elif not _is_blank(txn_count_raw):
            _record_exception(
                uploaded_file,
                "NTSL_UNPARSEABLE_TXN_COUNT",
                f"NTSL line '{description}': No of Txns {txn_count_raw!r} is not numeric -- "
                "stored as NULL.",
                severity=ValidationSeverity.WARNING,
                row_reference=row_ref,
            )

        lines.append(
            NtslSettlementLine(
                payment_module=uploaded_file.payment_module,
                uploaded_file=uploaded_file,
                settlement_cycle=uploaded_file.settlement_cycle,
                business_date=uploaded_file.business_date,
                row_reference=row_ref,
                staging_row_id=staging_row.id,
                description=description,
                txn_count=txn_count,
                debit=debit_num if debit_num is not None else Decimal("0"),
                credit=credit_num if credit_num is not None else Decimal("0"),
                is_final_settlement=description.lower() == _NTSL_FINAL_SETTLEMENT_DESCRIPTION,
            )
        )

    if empty_description_refs:
        _record_exception(
            uploaded_file,
            "NTSL_EMPTY_DESCRIPTION_ROWS_SKIPPED",
            f"{len(empty_description_refs)} row(s) with numeric values but an empty Description "
            f"were not loaded: {', '.join(empty_description_refs)}",
            severity=ValidationSeverity.INFO,
        )

    # --- header cross-check (non-fatal) ---
    expected_cycle = uploaded_file.settlement_cycle.cycle_code if uploaded_file.settlement_cycle else None
    if title_match is None:
        _record_exception(
            uploaded_file,
            "NTSL_HEADER_NOT_FOUND",
            "No title row matching 'as on DD-MM-YYYY ( <cycle> ' found -- business date and "
            "cycle could not be cross-checked against the file's own header.",
            severity=ValidationSeverity.WARNING,
        )
    else:
        header_date_raw, header_cycle = title_match.group(1), title_match.group(2)
        try:
            header_date = datetime.strptime(header_date_raw, "%d-%m-%Y").date()
        except ValueError:
            header_date = None
        mismatches = []
        if header_date != uploaded_file.business_date:
            mismatches.append(f"date {header_date_raw} vs business_date {uploaded_file.business_date}")
        if header_cycle != expected_cycle:
            mismatches.append(f"cycle {header_cycle} vs settlement_cycle {expected_cycle}")
        if mismatches:
            _record_exception(
                uploaded_file,
                "NTSL_HEADER_MISMATCH",
                "NTSL title row disagrees with the UploadedFile: " + "; ".join(mismatches),
                severity=ValidationSeverity.WARNING,
            )

    final_count = sum(1 for line in lines if line.is_final_settlement)
    if final_count != 1:
        _record_exception(
            uploaded_file,
            "NTSL_FINAL_SETTLEMENT_COUNT",
            f"Expected exactly 1 'Final Settlement Amount' line, found {final_count}",
            severity=ValidationSeverity.WARNING,
        )

    if lines:
        NtslSettlementLine.objects.bulk_create(lines, ignore_conflicts=True, batch_size=_STAGING_BATCH_SIZE)
    loaded_count = NtslSettlementLine.objects.filter(uploaded_file=uploaded_file).count()

    uploaded_file.valid_record_count = loaded_count
    uploaded_file.processing_status = ProcessingStatus.LOADED
    uploaded_file.save(update_fields=["valid_record_count", "processing_status"])

    return {"uploaded_file_id": uploaded_file_id, "loaded_count": loaded_count, "halted": False}
