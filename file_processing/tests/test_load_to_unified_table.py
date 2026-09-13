"""
Exercises Stage 4 (`load_to_unified_table`) -- the previously-stubbed
`NotImplementedError` task that now populates `transactions.TransactionRecord`
-- for all six in-scope UPI file types (CBS GL Extract, Raw Data, Merchant
Raw Data, Merchant Raw Data BBP, DRC, Timeout, Adjustment; 7 `FileTypeConfig`s
across those 6 types -- NTSL is out of scope, see doubts.md).

Each `FileTypeConfigTests`-style class runs the real pipeline stage functions
directly (`format_sniff_and_parse` -> `validate_structure_and_content` ->
`normalize_rows` -> `load_to_unified_table`) against the real sample files
under `sample_decrypted_files/`, following the same direct-call pattern used
in `test_cbs_parser.py` / `test_date_selection.py` -- no Celery broker
involved. `FileTypeConfig` fixtures are built locally per the existing
`_build_cbs_config()`-style convention (not read from the
`seed_file_type_configs` management command, which exists for real
deployments/manual verification, not test fixtures -- see that command's
docstring for why).
"""
from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.test import TestCase

from core.models import Direction, PaymentModule, RegisterType, SettlementCycle
from file_processing.models import (
    ContainerFormat,
    DateValidationMode,
    FileTypeConfig,
    FileValidationException,
    PayloadFormat,
    ProcessingStatus,
    UploadedFile,
    ValidationSeverity,
)
from file_processing.tasks import (
    _resolve_column_mapping,
    format_sniff_and_parse,
    load_to_unified_table,
    normalize_rows,
    validate_structure_and_content,
)
from file_processing.tests.test_pagination_strip import CBS_ARTIFACT_PATTERNS
from transactions.models import TransactionRecord

SAMPLE_ROOT = Path(__file__).resolve().parents[2] / "sample_decrypted_files"
CBS_SAMPLE = Path(__file__).resolve().parents[2] / "14072026" / "cbs_report" / "b5rg090oa.xls"


def _build_payment_module() -> PaymentModule:
    return PaymentModule.objects.create(code="UPI", name="UPI")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_uploaded_file(payment_module: PaymentModule, config: FileTypeConfig, path: Path, **overrides) -> UploadedFile:
    defaults = dict(
        filename=path.name,
        path=str(path),
        payment_module=payment_module,
        file_type_config=config,
        business_date=date(2026, 7, 14),
        declared_format=path.suffix,
        size_bytes=path.stat().st_size,
        sha256_hash=_sha256(path),
    )
    defaults.update(overrides)
    return UploadedFile.objects.create(**defaults)


def _run_pipeline(uploaded_file: UploadedFile) -> dict:
    """Runs stages 1-4 directly against a real EXACT_MATCH-mode UploadedFile
    and returns load_to_unified_table's result context."""
    ctx = format_sniff_and_parse(uploaded_file.id)
    ctx = validate_structure_and_content(ctx)
    ctx = normalize_rows(ctx)
    return load_to_unified_table(ctx)


class RawDataLoadTests(TestCase):
    """Headerless TX rows, RRN at fixed position 4, amount in paise at
    position 8 -- confirmed via sample_decrypted_files/UPIRAWDATAACQSSK...
    (see doubts.md's Raw Data / Merchant Raw Data resolutions)."""

    def _build_config(self, payment_module: PaymentModule) -> FileTypeConfig:
        return FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="RAW_DATA",
            register_type=RegisterType.RAW_DATA,
            payload_format=PayloadFormat.CSV,
            container_format=ContainerFormat.NONE,
            has_header=False,
            column_mapping={"rrn": "4", "amount": "8"},
            required_columns=["rrn"],
            amount_source_fields=["amount"],
            amount_scale_factor=Decimal("0.01"),
            rrn_expected_digit_length=12,
            pagination_artifact_patterns=[r"^HT\b", r"^FT\b"],
            date_validation_mode=DateValidationMode.EXACT_MATCH,
            is_cycle_wise=True,
            cycle_set=[f"{n}C" for n in range(1, 11)],
        )

    def test_loads_all_tx_rows_with_correct_rrn_amount_and_business_date(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        sample = SAMPLE_ROOT / "UPIRAWDATAACQSSK140726_1C.csv"
        uploaded_file = _build_uploaded_file(payment_module, config, sample)

        result = _run_pipeline(uploaded_file)
        self.assertFalse(result["halted"])

        uploaded_file.refresh_from_db()
        self.assertEqual(uploaded_file.processing_status, ProcessingStatus.LOADED)
        # 1812 raw lines = 1 "HT" + 1810 "TX" + 1 "FT" (confirmed via the real
        # sample file); both HT/FT are stripped by pagination_artifact_patterns.
        self.assertEqual(uploaded_file.valid_record_count, 1810)
        self.assertEqual(result["loaded_count"], 1810)
        self.assertEqual(result["skipped_count"], 0)

        records = TransactionRecord.objects.filter(uploaded_file=uploaded_file).order_by("id")
        self.assertEqual(records.count(), 1810)

        first = records.first()
        self.assertEqual(first.rrn, "775624108608")
        self.assertTrue(first.rrn_valid)
        self.assertEqual(first.amount, Decimal("3500.00"))  # 350000 paise
        self.assertEqual(first.direction, Direction.UNKNOWN)  # no confirmed derivation rule
        self.assertEqual(first.source_register_type, RegisterType.RAW_DATA)
        # No "date" column mapped for Raw Data -- business_date falls back to
        # the file-level value entered at upload.
        self.assertEqual(first.business_date, date(2026, 7, 14))
        self.assertEqual(first.extra_fields["raw_cells"]["4"], "775624108608")


class MerchantRawDataLoadTests(TestCase):
    """Same row shape as Raw Data, distinct RegisterType."""

    def _build_config(self, payment_module: PaymentModule) -> FileTypeConfig:
        return FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="MERCHANT_RAW_DATA",
            register_type=RegisterType.MERCHANT_RAW_DATA,
            payload_format=PayloadFormat.CSV,
            container_format=ContainerFormat.NONE,
            has_header=False,
            column_mapping={"rrn": "4", "amount": "8"},
            required_columns=["rrn"],
            amount_source_fields=["amount"],
            amount_scale_factor=Decimal("0.01"),
            rrn_expected_digit_length=12,
            pagination_artifact_patterns=[r"^HT\b", r"^FT\b"],
            date_validation_mode=DateValidationMode.EXACT_MATCH,
            is_cycle_wise=True,
            cycle_set=[f"{n}C" for n in range(1, 11)],
        )

    def test_loads_all_tx_rows_under_merchant_register_type(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        sample = SAMPLE_ROOT / "UPIMERCHANTRAWDATAACQSSK140726_1C.csv"
        uploaded_file = _build_uploaded_file(payment_module, config, sample)

        result = _run_pipeline(uploaded_file)
        self.assertFalse(result["halted"])

        records = TransactionRecord.objects.filter(uploaded_file=uploaded_file)
        self.assertGreater(records.count(), 0)
        self.assertTrue(all(r.source_register_type == RegisterType.MERCHANT_RAW_DATA for r in records))
        # Distinct classification from both RAW_DATA and the BBP variant.
        self.assertFalse(records.filter(source_register_type=RegisterType.RAW_DATA).exists())
        self.assertFalse(
            records.filter(source_register_type=RegisterType.MERCHANT_RAW_DATA_BBP).exists()
        )


class MerchantRawDataBbpLoadTests(TestCase):
    """Own RegisterType (project owner decision 2026-09-10, see doubts.md)
    despite identical row shape/parsing to standard Merchant Raw Data ACQ --
    confirms BBP volume lands under its own classification, not merged into
    RegisterType.MERCHANT_RAW_DATA."""

    def _build_config(self, payment_module: PaymentModule) -> FileTypeConfig:
        return FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="MERCHANT_RAW_DATA_BBP",
            register_type=RegisterType.MERCHANT_RAW_DATA_BBP,
            payload_format=PayloadFormat.CSV,
            container_format=ContainerFormat.NONE,
            has_header=False,
            column_mapping={"rrn": "4", "amount": "8"},
            required_columns=["rrn"],
            amount_source_fields=["amount"],
            amount_scale_factor=Decimal("0.01"),
            rrn_expected_digit_length=12,
            pagination_artifact_patterns=[r"^HT\b", r"^FT\b"],
            date_validation_mode=DateValidationMode.EXACT_MATCH,
            is_cycle_wise=True,
            cycle_set=["4C", "5C", "6C"],
        )

    def test_loads_bbp_row_under_its_own_register_type_with_settlement_cycle(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        cycle = SettlementCycle.objects.create(
            payment_module=payment_module,
            cycle_code="4C",
            cycle_type="FINANCIAL",
            start_time="00:00",
            end_time="04:00",
        )
        sample = SAMPLE_ROOT / "UPIBBPMERCHANTRAWDATAACQSSK140726_4C.csv"
        uploaded_file = _build_uploaded_file(payment_module, config, sample, settlement_cycle=cycle)

        result = _run_pipeline(uploaded_file)
        self.assertFalse(result["halted"])

        records = TransactionRecord.objects.filter(uploaded_file=uploaded_file)
        # Sample file has exactly 1 TX row (HT + 1 TX + FT).
        self.assertEqual(records.count(), 1)
        record = records.get()
        self.assertEqual(record.source_register_type, RegisterType.MERCHANT_RAW_DATA_BBP)
        self.assertEqual(record.rrn, "085435995859")
        self.assertEqual(record.amount, Decimal("177.00"))  # 17700 paise
        self.assertEqual(record.settlement_cycle, cycle)


class DrcLoadTests(TestCase):
    """Named header row; RRN col 7 (leading apostrophe stripped), "TXN Date"
    col 3 (DD/MM/YYYY), Amount col 13 (plain rupee decimal, not paise) --
    confirmed via sample_decrypted_files/Debit Reversal Confirmation
    Report__SSK_2026-07-14.csv."""

    def _build_config(self, payment_module: PaymentModule) -> FileTypeConfig:
        return FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="DRC",
            register_type=RegisterType.DRC,
            payload_format=PayloadFormat.CSV,
            container_format=ContainerFormat.NONE,
            has_header=True,
            column_mapping={"rrn": "7", "date": "3", "amount": "13"},
            required_columns=["rrn"],
            amount_source_fields=["amount"],
            amount_scale_factor=Decimal("1"),
            rrn_expected_digit_length=12,
            pagination_artifact_patterns=[],
            date_validation_mode=DateValidationMode.EXACT_MATCH,
            is_cycle_wise=False,
        )

    def test_loads_all_data_rows_with_apostrophe_stripped_rrn(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        sample = SAMPLE_ROOT / "Debit Reversal Confirmation Report__SSK_2026-07-14.csv"
        uploaded_file = _build_uploaded_file(payment_module, config, sample)

        result = _run_pipeline(uploaded_file)
        self.assertFalse(result["halted"])

        uploaded_file.refresh_from_db()
        self.assertEqual(uploaded_file.processing_status, ProcessingStatus.LOADED)
        # 274 raw lines = 1 header + 273 data rows.
        self.assertEqual(uploaded_file.valid_record_count, 273)

        records = TransactionRecord.objects.filter(uploaded_file=uploaded_file).order_by("id")
        self.assertEqual(records.count(), 273)

        first = records.first()
        self.assertEqual(first.rrn, "949114317390")  # leading "'" stripped
        self.assertTrue(first.rrn_valid)
        self.assertEqual(first.amount, Decimal("1.00"))
        self.assertEqual(first.direction, Direction.UNKNOWN)
        # "TXN Date" resolves to 2026-07-14 for every row (confirmed constant),
        # matching the file-level business_date entered at upload.
        self.assertEqual(first.business_date, date(2026, 7, 14))


class TimeoutLoadTests(TestCase):
    """Identical column layout to DRC."""

    def _build_config(self, payment_module: PaymentModule) -> FileTypeConfig:
        return FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="TIMEOUT",
            register_type=RegisterType.TIMEOUT,
            payload_format=PayloadFormat.CSV,
            container_format=ContainerFormat.NONE,
            has_header=True,
            column_mapping={"rrn": "7", "date": "3", "amount": "13"},
            required_columns=["rrn"],
            amount_source_fields=["amount"],
            amount_scale_factor=Decimal("1"),
            rrn_expected_digit_length=12,
            pagination_artifact_patterns=[],
            date_validation_mode=DateValidationMode.EXACT_MATCH,
            is_cycle_wise=False,
        )

    def test_loads_all_data_rows(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        sample = SAMPLE_ROOT / "UPI Time Out Cases Report_SSK_2026-07-14.csv"
        uploaded_file = _build_uploaded_file(payment_module, config, sample)

        result = _run_pipeline(uploaded_file)
        self.assertFalse(result["halted"])

        # 31 raw lines = 1 header + 30 data rows.
        records = TransactionRecord.objects.filter(uploaded_file=uploaded_file)
        self.assertEqual(records.count(), 30)
        self.assertTrue(all(r.source_register_type == RegisterType.TIMEOUT for r in records))


class AdjustmentLoadTests(TestCase):
    """Adjdate (col 3, DD-MM-YYYY) is the canonical business_date -- resolved
    2026-09-09, see doubts.md -- and can genuinely differ from the file's
    nominal upload-time business_date (confirmed: one real sample row has
    Adjdate 2026-07-15 in a file otherwise dated 2026-07-14). Txndate (col 8)
    is deliberately NOT mapped as a canonical field, so it lands in
    extra_fields.raw_cells alongside Adjustment's other ~46 columns rather
    than being dropped."""

    def _build_config(self, payment_module: PaymentModule) -> FileTypeConfig:
        return FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="ADJUSTMENT",
            register_type=RegisterType.ADJUSTMENT,
            payload_format=PayloadFormat.CSV,
            container_format=ContainerFormat.NONE,
            has_header=True,
            column_mapping={"rrn": "10", "date": "3", "amount": "17"},
            required_columns=["rrn", "date"],
            amount_source_fields=["amount"],
            amount_scale_factor=Decimal("1"),
            rrn_expected_digit_length=12,
            pagination_artifact_patterns=[],
            date_validation_mode=DateValidationMode.EXACT_MATCH,
            is_cycle_wise=False,
        )

    def test_loads_all_rows_using_adjdate_as_business_date(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        sample = SAMPLE_ROOT / "UPI Adjustment Report_SSK_2026-07-14.csv"
        uploaded_file = _build_uploaded_file(payment_module, config, sample)

        result = _run_pipeline(uploaded_file)
        self.assertFalse(result["halted"])

        # 258 raw lines = 1 header + 257 data rows.
        records = TransactionRecord.objects.filter(uploaded_file=uploaded_file).order_by("id")
        self.assertEqual(records.count(), 257)

        first = records.first()
        self.assertEqual(first.rrn, "619507519732")  # leading "'" stripped
        self.assertEqual(first.business_date, date(2026, 7, 14))  # row's own Adjdate
        # Txndate (raw cell "8") is retained in extra_fields, not dropped.
        self.assertEqual(first.extra_fields["raw_cells"]["8"], "14-07-2026")

        # Confirms per-row Adjdate resolution actually overrides the file's
        # nominal business_date, not just coincidentally matching it: at
        # least one row's Adjdate is 2026-07-15 despite the file/upload
        # being for 2026-07-14 (see doubts.md's Adjustment resolution).
        differing_date_records = records.filter(business_date=date(2026, 7, 15))
        self.assertTrue(differing_date_records.exists())
        self.assertFalse(differing_date_records.filter(business_date=uploaded_file.business_date).exists())


class CbsEndToEndLoadTests(TestCase):
    """Extends the RANGE_CONTAINS date-selection flow covered in
    test_date_selection.py through Stage 4, confirming CBS -- resolved to
    business_date=2026-07-14 via the existing "Select business date" admin
    action stand-in -- reaches transactions.TransactionRecord too, not just
    NORMALIZED, and actually loads rows (not just 0-row LOADED).

    CBS's real ~154,661-row report is split across 3 worksheets ("Report",
    "Report1", "Report2") purely by Excel's row-count limit -- one
    continuous report, not 3 separate reports -- but each worksheet has its
    own stable column layout (confirmed by scanning every row's ss:Index
    shape across the complete real file, not a sample; see
    seed_file_type_configs.py's CBS entry for the full per-worksheet index
    table and cross-check method). column_mapping below uses the new
    per-worksheet form (`file_processing.tasks._resolve_column_mapping`) so
    Report1/Report2's rows -- which hold the bulk of 2026-07-14's rows --
    resolve remarks/amount correctly instead of being silently skipped."""

    def _build_config(self, payment_module: PaymentModule) -> FileTypeConfig:
        return FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="CBS_GL_EXTRACT",
            register_type=RegisterType.CBS_GL_EXTRACT,
            payload_format=PayloadFormat.SPREADSHEETML_XML,
            container_format=ContainerFormat.NONE,
            column_mapping={
                "date": {"Report": 1, "Report1": 1, "Report2": 1, "_default": 1},
                "remarks": {"Report": 3, "Report1": 2, "Report2": 2, "_default": 3},
                "withdrawals": {"Report": 7, "Report1": 4, "Report2": 5, "_default": 7},
                "deposits": {"Report": 10, "Report1": 5, "Report2": 6, "_default": 10},
                "balance": {"Report": 12, "Report1": 6, "Report2": 9, "_default": 12},
            },
            required_columns=["date"],
            amount_source_fields=["withdrawals", "deposits"],
            direction_derivation_rule="remarks_prefix:By=CREDIT,To=DEBIT",
            rrn_expected_digit_length=12,
            pagination_artifact_patterns=CBS_ARTIFACT_PATTERNS,
            date_validation_mode=DateValidationMode.RANGE_CONTAINS,
        )

    def test_cbs_loads_rows_from_all_three_worksheets_with_correct_per_worksheet_mapping(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        uploaded_file = _build_uploaded_file(
            payment_module, config, CBS_SAMPLE, business_date=None
        )

        parse_ctx = format_sniff_and_parse(uploaded_file.id)
        validate_structure_and_content(parse_ctx)
        uploaded_file.refresh_from_db()
        self.assertEqual(uploaded_file.processing_status, ProcessingStatus.AWAITING_DATE_SELECTION)

        # Simulate the admin action's resume step (see
        # UploadedFileAdmin.select_business_date in admin.py).
        uploaded_file.business_date = date(2026, 7, 14)
        uploaded_file.processing_status = ProcessingStatus.VALIDATED
        uploaded_file.save(update_fields=["business_date", "processing_status"])

        normalize_ctx = normalize_rows(uploaded_file.pending_pipeline_context)
        load_result = load_to_unified_table(normalize_ctx)
        self.assertFalse(load_result["halted"])

        uploaded_file.refresh_from_db()
        self.assertEqual(uploaded_file.processing_status, ProcessingStatus.LOADED)
        # Per-worksheet mapping now resolves remarks/amount for all three
        # worksheets, so rows actually load instead of the previous 0.
        self.assertGreater(load_result["loaded_count"], 0)

        records = TransactionRecord.objects.filter(uploaded_file=uploaded_file)
        self.assertEqual(records.count(), load_result["loaded_count"])

        # Rows were loaded from more than just worksheet "Report" -- the
        # worksheet name is stored inside row_reference ("<worksheet>:<row
        # number>", see load_to_unified_table's row_ref construction).
        worksheets_seen = {r.row_reference.split(":", 1)[0] for r in records}
        self.assertTrue(worksheets_seen & {"Report1", "Report2"})

        # Spot-check: every row whose remarks matched the "By/To UPI/<RRN>/"
        # pattern (i.e. rrn was extracted at all -- see _extract_cbs_rrn,
        # which only ever returns an rrn when that pattern matched) got a
        # real CREDIT/DEBIT direction from the same remarks-prefix rule, and
        # every loaded row has a genuine positive amount -- confirms
        # remarks/amount were decoded from the *correct* per-worksheet
        # columns, not just that some non-zero count loaded. Rows whose
        # remarks aren't UPI-shaped (e.g. non-UPI GL entries) legitimately
        # keep Direction.UNKNOWN and no rrn -- that's not this bug.
        for record in records:
            self.assertGreater(record.amount, Decimal("0"))
            if record.rrn:
                self.assertRegex(record.rrn, r"^\d+$")
                self.assertIn(record.direction, (Direction.CREDIT, Direction.DEBIT))

    def test_no_data_rows_are_skipped_as_unloadable_once_per_worksheet_mapping_is_used(self):
        """Regression guard for the original bug: CBS previously reached
        ProcessingStatus.LOADED with loaded_count == 0 and every row logged
        as SKIPPED_UNLOADABLE_ROW, because the old flat column_mapping only
        matched worksheet "Report"'s layout."""
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        uploaded_file = _build_uploaded_file(
            payment_module, config, CBS_SAMPLE, business_date=None
        )

        parse_ctx = format_sniff_and_parse(uploaded_file.id)
        validate_structure_and_content(parse_ctx)
        uploaded_file.refresh_from_db()

        uploaded_file.business_date = date(2026, 7, 14)
        uploaded_file.processing_status = ProcessingStatus.VALIDATED
        uploaded_file.save(update_fields=["business_date", "processing_status"])

        normalize_ctx = normalize_rows(uploaded_file.pending_pipeline_context)
        load_result = load_to_unified_table(normalize_ctx)

        self.assertFalse(
            FileValidationException.objects.filter(
                uploaded_file=uploaded_file, rule_code="SKIPPED_UNLOADABLE_ROW"
            ).exists()
        )
        self.assertEqual(load_result["skipped_count"], 0)


class ResolveColumnMappingUnitTests(TestCase):
    """Unit tests for file_processing.tasks._resolve_column_mapping in
    isolation -- covers the flat (back-compat), per-worksheet, and
    "_default" fallback shapes without needing a real config/file."""

    def _config(self, column_mapping):
        # A lightweight stand-in is enough -- _resolve_column_mapping only
        # ever reads `.column_mapping`.
        class _FakeConfig:
            pass

        cfg = _FakeConfig()
        cfg.column_mapping = column_mapping
        return cfg

    def test_flat_mapping_is_returned_unchanged_regardless_of_worksheet(self):
        """Back-compat: the six non-CBS types' existing flat
        {field_name: col_ref} configs must resolve identically no matter
        what worksheet_name is passed (they have none -- worksheet is None
        for CSV rows)."""
        flat = {"rrn": "4", "amount": "8"}
        config = self._config(flat)

        self.assertEqual(_resolve_column_mapping(config, None), flat)
        self.assertEqual(_resolve_column_mapping(config, "Sheet1"), flat)

    def test_per_worksheet_mapping_resolves_recognized_sheet_name(self):
        nested = {
            "date": {"Report": 1, "Report1": 1, "_default": 1},
            "remarks": {"Report": 3, "Report1": 2, "_default": 3},
        }
        config = self._config(nested)

        self.assertEqual(
            _resolve_column_mapping(config, "Report1"),
            {"date": 1, "remarks": 2},
        )
        self.assertEqual(
            _resolve_column_mapping(config, "Report"),
            {"date": 1, "remarks": 3},
        )

    def test_per_worksheet_mapping_falls_back_to_default_for_unrecognized_sheet_name(self):
        nested = {
            "date": {"Report": 1, "_default": 1},
            "remarks": {"Report": 3, "_default": 99},
        }
        config = self._config(nested)

        self.assertEqual(
            _resolve_column_mapping(config, "SomeUnexpectedSheet"),
            {"date": 1, "remarks": 99},
        )


class LoadToUnifiedTableUnitTests(TestCase):
    """Fast, non-file-backed unit tests against synthetic normalize_rows-
    shaped contexts -- covers the skip-on-unparseable-amount path and
    idempotent-retry behaviour without needing a real sample file."""

    def _build_config(self, payment_module: PaymentModule) -> FileTypeConfig:
        return FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="RAW_DATA",
            register_type=RegisterType.RAW_DATA,
            payload_format=PayloadFormat.CSV,
            container_format=ContainerFormat.NONE,
            has_header=False,
        )

    def _build_uploaded_file(self, payment_module, config) -> UploadedFile:
        return UploadedFile.objects.create(
            filename="synthetic.csv",
            path="synthetic.csv",
            payment_module=payment_module,
            file_type_config=config,
            business_date=date(2026, 7, 14),
            declared_format=".csv",
            size_bytes=1,
            sha256_hash="synthetic-hash",
        )

    def test_no_longer_raises_not_implemented_error(self):
        """The load_to_unified_table task used to unconditionally raise
        NotImplementedError -- confirms it now runs to completion instead."""
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        uploaded_file = self._build_uploaded_file(payment_module, config)

        context = {
            "uploaded_file_id": uploaded_file.id,
            "halted": False,
            "rows": [
                {
                    "row_number": 2,
                    "worksheet": None,
                    "cells": {"4": "123456789012", "8": "50000"},
                    "rrn": "123456789012",
                    "rrn_valid": True,
                    "direction": Direction.UNKNOWN,
                    "amount": "500.00",
                    "row_date": None,
                }
            ],
        }

        result = load_to_unified_table(context)  # must not raise

        self.assertEqual(result["loaded_count"], 1)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(TransactionRecord.objects.filter(uploaded_file=uploaded_file).count(), 1)

    def test_row_with_unparseable_amount_is_skipped_not_loaded(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        uploaded_file = self._build_uploaded_file(payment_module, config)

        context = {
            "uploaded_file_id": uploaded_file.id,
            "halted": False,
            "rows": [
                {
                    "row_number": 2,
                    "worksheet": None,
                    "cells": {"4": "123456789012", "8": None},
                    "rrn": "123456789012",
                    "rrn_valid": True,
                    "direction": Direction.UNKNOWN,
                    "amount": None,  # normalize_rows already logged UNPARSEABLE_AMOUNT
                    "row_date": None,
                }
            ],
        }

        result = load_to_unified_table(context)

        self.assertEqual(result["loaded_count"], 0)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(TransactionRecord.objects.filter(uploaded_file=uploaded_file).count(), 0)
        self.assertTrue(
            FileValidationException.objects.filter(
                uploaded_file=uploaded_file,
                rule_code="SKIPPED_UNLOADABLE_ROW",
                severity=ValidationSeverity.ERROR,
            ).exists()
        )

    def test_retried_load_is_idempotent_via_ignore_conflicts(self):
        """unique_row_per_uploaded_file (uploaded_file, row_reference) plus
        ignore_conflicts=True means a retried Celery task doesn't double-load
        rows -- the scenario that constraint was added for."""
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        uploaded_file = self._build_uploaded_file(payment_module, config)

        context = {
            "uploaded_file_id": uploaded_file.id,
            "halted": False,
            "rows": [
                {
                    "row_number": 2,
                    "worksheet": None,
                    "cells": {"4": "123456789012", "8": "50000"},
                    "rrn": "123456789012",
                    "rrn_valid": True,
                    "direction": Direction.UNKNOWN,
                    "amount": "500.00",
                    "row_date": None,
                }
            ],
        }

        load_to_unified_table(context)
        load_to_unified_table(context)  # simulated retry, identical input

        self.assertEqual(TransactionRecord.objects.filter(uploaded_file=uploaded_file).count(), 1)

    def test_halted_context_is_a_no_op(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        uploaded_file = self._build_uploaded_file(payment_module, config)

        context = {"uploaded_file_id": uploaded_file.id, "halted": True, "rows": []}
        result = load_to_unified_table(context)

        self.assertTrue(result["halted"])
        self.assertEqual(TransactionRecord.objects.filter(uploaded_file=uploaded_file).count(), 0)


class BuildProcessingChainIncludesStage4Tests(TestCase):
    """build_processing_chain now appends load_to_unified_table for
    EXACT_MATCH file types (RANGE_CONTAINS types still stop after stage 2,
    unchanged -- see admin.py's resume step for their stage 3+4)."""

    def test_exact_match_chain_has_all_four_stages(self):
        from file_processing.tasks import build_processing_chain

        payment_module = _build_payment_module()
        config = FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="DRC",
            register_type=RegisterType.DRC,
            payload_format=PayloadFormat.CSV,
            date_validation_mode=DateValidationMode.EXACT_MATCH,
        )
        uploaded_file = UploadedFile.objects.create(
            filename="x.csv",
            path="x.csv",
            payment_module=payment_module,
            file_type_config=config,
            business_date=date(2026, 7, 14),
            declared_format=".csv",
            size_bytes=1,
            sha256_hash="chain-test-hash",
        )

        built = build_processing_chain(uploaded_file.id)

        task_names = [t.task for t in built.tasks]
        self.assertEqual(
            task_names,
            [
                "file_processing.tasks.format_sniff_and_parse",
                "file_processing.tasks.validate_structure_and_content",
                "file_processing.tasks.normalize_rows",
                "file_processing.tasks.load_to_unified_table",
            ],
        )
