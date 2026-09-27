"""
Exercises the staging -> fact pipeline (schema redesign approved
2026-09-23; replaces the former test_load_to_unified_table.py):
`format_sniff_and_parse` -> `validate_structure_and_content` ->
`load_to_staging` -> `normalize_rows` (reads staging) -> `promote_to_fact`,
for all six in-scope UPI file types (CBS GL Extract, Raw Data, Merchant Raw
Data, Merchant Raw Data BBP, DRC, Timeout, Adjustment -- NTSL never
reaches TransactionFact; its staging -> settlement-summary load is covered
in test_ntsl_load.py).

Real-data classes run the stage functions directly against the real sample
files under `sample_decrypted_files/` (same direct-call pattern as
`test_cbs_parser.py` / `test_date_selection.py` -- no Celery broker).
`FileTypeConfig` fixtures are built locally per the existing
`_build_cbs_config()`-style convention, not read from the
`seed_file_type_configs` command (see that command's docstring for why).
Every RRN/amount/direction assertion from the former
test_load_to_unified_table.py is preserved; assertions that used to read
`TransactionRecord.extra_fields["raw_cells"]` now read the same raw cell
through the fact's staging row (`TransactionFact.staging_row()`).
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
    load_to_staging,
    normalize_rows,
    promote_to_fact,
    validate_structure_and_content,
)
from file_processing.tests.test_pagination_strip import CBS_ARTIFACT_PATTERNS
from transactions.models import (
    StagingAdjustment,
    StagingCbsGlExtract,
    StagingDrc,
    StagingMerchantRawData,
    StagingMerchantRawDataBbp,
    StagingRawData,
    StagingTimeout,
    TransactionFact,
)

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
    """Runs all 5 stages directly against a real EXACT_MATCH-mode
    UploadedFile and returns promote_to_fact's result context."""
    ctx = format_sniff_and_parse(uploaded_file.id)
    ctx = validate_structure_and_content(ctx)
    ctx = load_to_staging(ctx)
    ctx = normalize_rows(ctx)
    return promote_to_fact(ctx)


def _headerless_config(payment_module, *, file_type_code, register_type, direction_rule, cycle_set=None):
    return FileTypeConfig.objects.create(
        payment_module=payment_module,
        file_type_code=file_type_code,
        register_type=register_type,
        payload_format=PayloadFormat.CSV,
        container_format=ContainerFormat.NONE,
        has_header=False,
        column_mapping={"rrn": "4", "amount": "8"},
        required_columns=["rrn"],
        amount_source_fields=["amount"],
        amount_scale_factor=Decimal("0.01"),
        direction_derivation_rule=direction_rule,
        rrn_expected_digit_length=12,
        pagination_artifact_patterns=[r"^HT\b", r"^FT\b"],
        date_validation_mode=DateValidationMode.EXACT_MATCH,
        is_cycle_wise=True,
        cycle_set=cycle_set or [f"{n}C" for n in range(1, 11)],
    )


class RawDataLoadTests(TestCase):
    """Headerless TX rows, RRN at fixed position 4, amount in paise at
    position 8 -- confirmed via sample_decrypted_files/UPIRAWDATAACQSSK...
    (see doubts.md's Raw Data / Merchant Raw Data resolutions).

    Split into ACQ/ISS-specific configs (resolved 2026-09-12, implemented
    2026-09-16): Acquirer files -> DEBIT, Issuer files -> CREDIT, per the
    bank's SOP."""

    def test_acq_file_stages_then_loads_with_debit_direction(self):
        payment_module = _build_payment_module()
        config = _headerless_config(
            payment_module, file_type_code="RAW_DATA", register_type=RegisterType.RAW_DATA,
            direction_rule="constant:DEBIT",
        )
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

        # --- staging ---
        staged = StagingRawData.objects.filter(uploaded_file=uploaded_file).order_by("id")
        self.assertEqual(staged.count(), 1810)
        first_staged = staged.first()
        self.assertEqual(first_staged.rrn_raw, "775624108608")
        self.assertEqual(first_staged.amount_paise, "350000")  # raw, unscaled
        self.assertEqual(first_staged.raw_cells["4"], "775624108608")
        self.assertEqual(first_staged.row_reference, f":{first_staged.row_number}")

        # --- fact ---
        records = TransactionFact.objects.filter(uploaded_file=uploaded_file).order_by("id")
        self.assertEqual(records.count(), 1810)

        first = records.first()
        self.assertEqual(first.rrn, "775624108608")
        self.assertTrue(first.rrn_valid)
        self.assertEqual(first.amount, Decimal("3500.00"))  # 350000 paise
        self.assertEqual(first.direction, Direction.DEBIT)  # Acquirer file -> DEBIT
        self.assertEqual(first.source_register_type, RegisterType.RAW_DATA)
        # No "date" column mapped for Raw Data -- business_date falls back to
        # the file-level value entered at upload.
        self.assertEqual(first.business_date, date(2026, 7, 14))
        self.assertIsNone(first.txn_amount)  # Adjustment-only field
        # Lineage: the fact points back at its staging row.
        self.assertEqual(first.staging_row_id, first_staged.id)
        self.assertEqual(first.staging_row().raw_cells["4"], "775624108608")
        self.assertEqual(first.row_reference, first_staged.row_reference)
        # Every fact has lineage, and it's a distinct staging row each time.
        staging_ids = list(records.values_list("staging_row_id", flat=True))
        self.assertNotIn(None, staging_ids)
        self.assertEqual(len(set(staging_ids)), 1810)
        # Direction is constant for every row in an ACQ file, not just the first.
        self.assertTrue(all(r.direction == Direction.DEBIT for r in records))

    def test_iss_file_loads_with_credit_direction(self):
        payment_module = _build_payment_module()
        config = _headerless_config(
            payment_module, file_type_code="RAW_DATA", register_type=RegisterType.RAW_DATA,
            direction_rule="constant:CREDIT",
        )
        sample = SAMPLE_ROOT / "UPIRAWDATAISSSSK140726_1C.csv"
        uploaded_file = _build_uploaded_file(payment_module, config, sample)

        result = _run_pipeline(uploaded_file)
        self.assertFalse(result["halted"])

        records = TransactionFact.objects.filter(uploaded_file=uploaded_file)
        self.assertGreater(records.count(), 0)
        self.assertEqual(
            records.count(), StagingRawData.objects.filter(uploaded_file=uploaded_file).count()
        )
        self.assertTrue(all(r.direction == Direction.CREDIT for r in records))  # Issuer file -> CREDIT


class MerchantRawDataLoadTests(TestCase):
    """Same row shape as Raw Data, distinct RegisterType (and staging
    table). Split into ACQ/ISS configs for the same reason as
    RawDataLoadTests above."""

    def test_acq_file_loads_under_merchant_register_type_with_debit_direction(self):
        payment_module = _build_payment_module()
        config = _headerless_config(
            payment_module, file_type_code="MERCHANT_RAW_DATA",
            register_type=RegisterType.MERCHANT_RAW_DATA, direction_rule="constant:DEBIT",
        )
        sample = SAMPLE_ROOT / "UPIMERCHANTRAWDATAACQSSK140726_1C.csv"
        uploaded_file = _build_uploaded_file(payment_module, config, sample)

        result = _run_pipeline(uploaded_file)
        self.assertFalse(result["halted"])

        staged_count = StagingMerchantRawData.objects.filter(uploaded_file=uploaded_file).count()
        self.assertGreater(staged_count, 0)
        # Staged into the Merchant Raw Data table only, not Raw Data's.
        self.assertFalse(StagingRawData.objects.filter(uploaded_file=uploaded_file).exists())

        records = TransactionFact.objects.filter(uploaded_file=uploaded_file)
        self.assertEqual(records.count(), staged_count)
        self.assertTrue(all(r.source_register_type == RegisterType.MERCHANT_RAW_DATA for r in records))
        self.assertTrue(all(r.direction == Direction.DEBIT for r in records))
        # Distinct classification from both RAW_DATA and the BBP variant.
        self.assertFalse(records.filter(source_register_type=RegisterType.RAW_DATA).exists())
        self.assertFalse(
            records.filter(source_register_type=RegisterType.MERCHANT_RAW_DATA_BBP).exists()
        )

    def test_iss_file_loads_under_merchant_register_type_with_credit_direction(self):
        payment_module = _build_payment_module()
        config = _headerless_config(
            payment_module, file_type_code="MERCHANT_RAW_DATA",
            register_type=RegisterType.MERCHANT_RAW_DATA, direction_rule="constant:CREDIT",
        )
        sample = SAMPLE_ROOT / "UPIMERCHANTRAWDATAISSSSK140726_1C.csv"
        uploaded_file = _build_uploaded_file(payment_module, config, sample)

        result = _run_pipeline(uploaded_file)
        self.assertFalse(result["halted"])

        records = TransactionFact.objects.filter(uploaded_file=uploaded_file)
        self.assertGreater(records.count(), 0)
        self.assertTrue(all(r.source_register_type == RegisterType.MERCHANT_RAW_DATA for r in records))
        self.assertTrue(all(r.direction == Direction.CREDIT for r in records))


class MerchantRawDataBbpLoadTests(TestCase):
    """Own RegisterType and own staging table (project owner decision
    2026-09-10, see doubts.md) despite identical row shape/parsing to
    standard Merchant Raw Data ACQ. ACQ-only, so constant DEBIT."""

    def test_loads_bbp_row_under_its_own_register_type_with_settlement_cycle(self):
        payment_module = _build_payment_module()
        config = _headerless_config(
            payment_module, file_type_code="MERCHANT_RAW_DATA_BBP",
            register_type=RegisterType.MERCHANT_RAW_DATA_BBP, direction_rule="constant:DEBIT",
            cycle_set=["4C", "5C", "6C"],
        )
        cycle = SettlementCycle.objects.create(
            payment_module=payment_module,
            cycle_code="4C",
            cycle_type="AUTH",
            start_time="00:00",
            end_time="04:00",
        )
        sample = SAMPLE_ROOT / "UPIBBPMERCHANTRAWDATAACQSSK140726_4C.csv"
        uploaded_file = _build_uploaded_file(payment_module, config, sample, settlement_cycle=cycle)

        result = _run_pipeline(uploaded_file)
        self.assertFalse(result["halted"])

        staged = StagingMerchantRawDataBbp.objects.filter(uploaded_file=uploaded_file)
        self.assertEqual(staged.count(), 1)
        self.assertEqual(staged.get().rrn_raw, "085435995859")
        self.assertEqual(staged.get().amount_paise, "17700")
        self.assertFalse(StagingMerchantRawData.objects.filter(uploaded_file=uploaded_file).exists())

        records = TransactionFact.objects.filter(uploaded_file=uploaded_file)
        # Sample file has exactly 1 TX row (HT + 1 TX + FT).
        self.assertEqual(records.count(), 1)
        record = records.get()
        self.assertEqual(record.source_register_type, RegisterType.MERCHANT_RAW_DATA_BBP)
        self.assertEqual(record.rrn, "085435995859")
        self.assertEqual(record.amount, Decimal("177.00"))  # 17700 paise
        self.assertEqual(record.direction, Direction.DEBIT)  # ACQ-only -> constant DEBIT
        self.assertEqual(record.settlement_cycle, cycle)
        self.assertEqual(record.staging_row_id, staged.get().id)


def _dispute_config(payment_module, *, file_type_code, register_type, direction_rule):
    """DRC and Timeout share an identical column layout (confirmed)."""
    return FileTypeConfig.objects.create(
        payment_module=payment_module,
        file_type_code=file_type_code,
        register_type=register_type,
        payload_format=PayloadFormat.CSV,
        container_format=ContainerFormat.NONE,
        has_header=True,
        column_mapping={"rrn": "7", "date": "3", "amount": "13"},
        required_columns=["rrn"],
        amount_source_fields=["amount"],
        amount_scale_factor=Decimal("1"),
        direction_derivation_rule=direction_rule,
        rrn_expected_digit_length=12,
        pagination_artifact_patterns=[],
        date_validation_mode=DateValidationMode.EXACT_MATCH,
        is_cycle_wise=False,
    )


class DrcLoadTests(TestCase):
    """Named header row; RRN col 7 (leading apostrophe stripped at
    normalization, kept verbatim in staging), "TXN Date" col 3 (DD/MM/YYYY),
    Amount col 13 (plain rupee decimal, not paise). Direction: confirmed by
    project owner (2026-09-16, quoting the SOP) -- constant DEBIT."""

    def test_loads_all_data_rows_with_apostrophe_stripped_rrn(self):
        payment_module = _build_payment_module()
        config = _dispute_config(
            payment_module, file_type_code="DRC", register_type=RegisterType.DRC,
            direction_rule="constant:DEBIT",
        )
        sample = SAMPLE_ROOT / "Debit Reversal Confirmation Report__SSK_2026-07-14.csv"
        uploaded_file = _build_uploaded_file(payment_module, config, sample)

        result = _run_pipeline(uploaded_file)
        self.assertFalse(result["halted"])

        uploaded_file.refresh_from_db()
        self.assertEqual(uploaded_file.processing_status, ProcessingStatus.LOADED)
        # 274 raw lines = 1 header + 273 data rows.
        self.assertEqual(uploaded_file.valid_record_count, 273)

        staged = StagingDrc.objects.filter(uploaded_file=uploaded_file).order_by("id")
        self.assertEqual(staged.count(), 273)
        first_staged = staged.first()
        # Raw form: whatever the parser produced for col 7, not cleaned.
        self.assertEqual(first_staged.rrn_raw, first_staged.raw_cells["7"])
        self.assertEqual(first_staged.rrn_raw.lstrip("'"), "949114317390")
        self.assertEqual(first_staged.txn_date_raw, "14/07/2026")
        self.assertEqual(first_staged.amount_raw, first_staged.raw_cells["13"])

        records = TransactionFact.objects.filter(uploaded_file=uploaded_file).order_by("id")
        self.assertEqual(records.count(), 273)

        first = records.first()
        self.assertEqual(first.rrn, "949114317390")  # leading "'" stripped
        self.assertTrue(first.rrn_valid)
        self.assertEqual(first.amount, Decimal("1.00"))
        self.assertEqual(first.direction, Direction.DEBIT)
        self.assertTrue(all(r.direction == Direction.DEBIT for r in records))
        # "TXN Date" resolves to 2026-07-14 for every row (confirmed constant),
        # matching the file-level business_date entered at upload.
        self.assertEqual(first.business_date, date(2026, 7, 14))
        self.assertEqual(first.staging_row_id, first_staged.id)


class TimeoutLoadTests(TestCase):
    """Identical column layout to DRC. Direction: confirmed by project
    owner (2026-09-16, quoting the SOP) -- constant CREDIT."""

    def test_loads_all_data_rows(self):
        payment_module = _build_payment_module()
        config = _dispute_config(
            payment_module, file_type_code="TIMEOUT", register_type=RegisterType.TIMEOUT,
            direction_rule="constant:CREDIT",
        )
        sample = SAMPLE_ROOT / "UPI Time Out Cases Report_SSK_2026-07-14.csv"
        uploaded_file = _build_uploaded_file(payment_module, config, sample)

        result = _run_pipeline(uploaded_file)
        self.assertFalse(result["halted"])

        # 31 raw lines = 1 header + 30 data rows.
        self.assertEqual(StagingTimeout.objects.filter(uploaded_file=uploaded_file).count(), 30)
        self.assertFalse(StagingDrc.objects.filter(uploaded_file=uploaded_file).exists())
        records = TransactionFact.objects.filter(uploaded_file=uploaded_file)
        self.assertEqual(records.count(), 30)
        self.assertTrue(all(r.source_register_type == RegisterType.TIMEOUT for r in records))
        self.assertTrue(all(r.direction == Direction.CREDIT for r in records))


class AdjustmentLoadTests(TestCase):
    """Adjdate (col 3, DD-MM-YYYY) is the canonical business_date -- resolved
    2026-09-09, see doubts.md -- and can genuinely differ from the file's
    nominal upload-time business_date. Txndate (col 8) is not a canonical
    fact field; it is retained in the staging row's raw_cells. Amount: the
    fact's `amount` is Adjamount (col 17, resolved 2026-09-17). Direction:
    constant CREDIT (project owner, 2026-09-16, quoting the SOP)."""

    def _build_config(self, payment_module: PaymentModule, *, column_mapping) -> FileTypeConfig:
        return FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="ADJUSTMENT",
            register_type=RegisterType.ADJUSTMENT,
            payload_format=PayloadFormat.CSV,
            container_format=ContainerFormat.NONE,
            has_header=True,
            column_mapping=column_mapping,
            required_columns=["rrn", "date"],
            amount_source_fields=["amount"],
            amount_scale_factor=Decimal("1"),
            direction_derivation_rule="constant:CREDIT",
            rrn_expected_digit_length=12,
            pagination_artifact_patterns=[],
            date_validation_mode=DateValidationMode.EXACT_MATCH,
            is_cycle_wise=False,
        )

    def test_loads_all_rows_using_adjdate_as_business_date(self):
        """Uses a minimal column_mapping (rrn/date/amount only, i.e. the
        seeded config before txn_amount/txn_date were added 2026-09-27) to
        prove unmapped typed columns stay NULL."""
        payment_module = _build_payment_module()
        config = self._build_config(payment_module, column_mapping={"rrn": "10", "date": "3", "amount": "17"})
        sample = SAMPLE_ROOT / "UPI Adjustment Report_SSK_2026-07-14.csv"
        uploaded_file = _build_uploaded_file(payment_module, config, sample)

        result = _run_pipeline(uploaded_file)
        self.assertFalse(result["halted"])

        # 258 raw lines = 1 header + 257 data rows.
        staged = StagingAdjustment.objects.filter(uploaded_file=uploaded_file).order_by("id")
        self.assertEqual(staged.count(), 257)
        first_staged = staged.first()
        self.assertEqual(first_staged.adj_date_raw, first_staged.raw_cells["3"])
        self.assertEqual(first_staged.adj_amount_raw, first_staged.raw_cells["17"])
        # Not in this config's column_mapping -> typed columns stay NULL,
        # values remain available in raw_cells.
        self.assertIsNone(first_staged.txn_date_raw)
        self.assertIsNone(first_staged.txn_amount_raw)

        records = TransactionFact.objects.filter(uploaded_file=uploaded_file).order_by("id")
        self.assertEqual(records.count(), 257)

        first = records.first()
        self.assertEqual(first.rrn, "619507519732")  # leading "'" stripped
        self.assertEqual(first.business_date, date(2026, 7, 14))  # row's own Adjdate
        self.assertEqual(first.direction, Direction.CREDIT)
        self.assertTrue(all(r.direction == Direction.CREDIT for r in records))
        # Txndate (raw cell "8") is retained (now in staging), not dropped.
        self.assertEqual(first.staging_row().raw_cells["8"], "14-07-2026")
        # No txn_amount mapping in this config -> txn_amount not populated.
        self.assertFalse(records.exclude(txn_amount=None).exists())

        # Confirms per-row Adjdate resolution actually overrides the file's
        # nominal business_date, not just coincidentally matching it: at
        # least one row's Adjdate is 2026-07-15 despite the file/upload
        # being for 2026-07-14 (see doubts.md's Adjustment resolution).
        differing_date_records = records.filter(business_date=date(2026, 7, 15))
        self.assertTrue(differing_date_records.exists())
        self.assertFalse(differing_date_records.filter(business_date=uploaded_file.business_date).exists())

    def test_txn_amount_populated_from_txnamount_and_amount_is_adjamount(self):
        """With "txn_amount"/"txn_date" keys present in column_mapping
        (Txnamount = col 16, Txndate = col 8 -- the positions already
        documented in doubts.md / seed_file_type_configs.py), txn_amount is
        populated from Txnamount while amount stays Adjamount (col 17).
        Mirrors the seeded ADJUSTMENT config (keys added with the project
        owner's OK, 2026-09-27)."""
        payment_module = _build_payment_module()
        config = self._build_config(
            payment_module,
            column_mapping={"rrn": "10", "date": "3", "amount": "17", "txn_date": "8", "txn_amount": "16"},
        )
        sample = SAMPLE_ROOT / "UPI Adjustment Report_SSK_2026-07-14.csv"
        uploaded_file = _build_uploaded_file(payment_module, config, sample)

        result = _run_pipeline(uploaded_file)
        self.assertEqual(result["loaded_count"], 257)

        first_staged = StagingAdjustment.objects.filter(uploaded_file=uploaded_file).order_by("id").first()
        self.assertEqual(first_staged.txn_date_raw, "14-07-2026")
        self.assertEqual(first_staged.txn_amount_raw, first_staged.raw_cells["16"])

        records = list(TransactionFact.objects.filter(uploaded_file=uploaded_file).order_by("id"))
        self.assertEqual(len(records), 257)
        differing = 0
        for record in records:
            raw = record.staging_row().raw_cells
            self.assertEqual(record.amount, Decimal(raw["17"]))  # Adjamount, unchanged
            self.assertIsNotNone(record.txn_amount)
            self.assertEqual(record.txn_amount, Decimal(raw["16"]))  # Txnamount
            if record.amount != record.txn_amount:
                differing += 1
        # doubts.md (2026-09-17): Txnamount and Adjamount differ in 180 of
        # the 257 real rows.
        self.assertEqual(differing, 180)

    def test_txn_amount_is_never_set_for_non_adjustment_register_types(self):
        payment_module = _build_payment_module()
        config = FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="DRC",
            register_type=RegisterType.DRC,
            payload_format=PayloadFormat.CSV,
        )
        uploaded_file = UploadedFile.objects.create(
            filename="synthetic-drc.csv", path="synthetic-drc.csv", payment_module=payment_module,
            file_type_config=config, business_date=date(2026, 7, 14), declared_format=".csv",
            size_bytes=1, sha256_hash="synthetic-drc-hash",
        )
        promote_to_fact({
            "uploaded_file_id": uploaded_file.id,
            "halted": False,
            "rows": [{
                "row_number": 2, "worksheet": None, "row_reference": ":2", "staging_row_id": 1,
                "rrn": "123456789012", "rrn_valid": True, "direction": Direction.DEBIT,
                "amount": "5.00", "txn_amount": "9.00", "row_date": None,
            }],
        })
        self.assertIsNone(TransactionFact.objects.get(uploaded_file=uploaded_file).txn_amount)


class CbsEndToEndLoadTests(TestCase):
    """CBS through the full staging -> pause -> resume -> fact flow.

    CBS's real ~154,661-row report is split across 3 worksheets ("Report",
    "Report1", "Report2") purely by Excel's row-count limit, each with its
    own stable column layout (see seed_file_type_configs.py's CBS entry).
    column_mapping below uses the per-worksheet form, and load_to_staging
    resolves it per row, so the typed staging columns come from the right
    cells on every worksheet."""

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

    def test_cbs_stages_pauses_resumes_and_loads_69239_rows_for_2026_07_14(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        uploaded_file = _build_uploaded_file(
            payment_module, config, CBS_SAMPLE, business_date=None
        )

        parse_ctx = format_sniff_and_parse(uploaded_file.id)
        validate_ctx = validate_structure_and_content(parse_ctx)
        kept_row_count = len(validate_ctx["rows"])
        stage_ctx = load_to_staging(validate_ctx)

        # --- pause happens AFTER staging, with a minimal context ---
        self.assertTrue(stage_ctx["halted"])
        uploaded_file.refresh_from_db()
        self.assertEqual(uploaded_file.processing_status, ProcessingStatus.AWAITING_DATE_SELECTION)
        self.assertEqual(uploaded_file.detected_dates, ["2026-07-13", "2026-07-14", "2026-07-15"])
        self.assertEqual(
            uploaded_file.pending_pipeline_context,
            {"uploaded_file_id": uploaded_file.id, "halted": True},
        )

        # --- staging holds every kept row, across all dates in the range ---
        staged = StagingCbsGlExtract.objects.filter(uploaded_file=uploaded_file)
        self.assertEqual(staged.count(), kept_row_count)
        self.assertEqual(stage_ctx["staged_count"], kept_row_count)
        self.assertEqual(
            set(staged.values_list("worksheet", flat=True).distinct()), {"Report", "Report1", "Report2"}
        )
        # Typed columns resolved through the per-worksheet mapping.
        report1_row = staged.filter(worksheet="Report1").exclude(remarks=None).order_by("id").first()
        self.assertEqual(report1_row.remarks, report1_row.raw_cells["2"])
        self.assertEqual(report1_row.raw_date, report1_row.raw_cells["1"])
        report_row = staged.filter(worksheet="Report").exclude(remarks=None).order_by("id").first()
        self.assertEqual(report_row.remarks, report_row.raw_cells["3"])
        self.assertEqual(report_row.row_reference, f"Report:{report_row.row_number}")

        # --- resume (what UploadedFileAdmin.select_business_date queues) ---
        uploaded_file.business_date = date(2026, 7, 14)
        uploaded_file.processing_status = ProcessingStatus.STAGED
        uploaded_file.save(update_fields=["business_date", "processing_status"])

        normalize_ctx = normalize_rows({"uploaded_file_id": uploaded_file.id, "halted": False})
        load_result = promote_to_fact(normalize_ctx)
        self.assertFalse(load_result["halted"])

        uploaded_file.refresh_from_db()
        self.assertEqual(uploaded_file.processing_status, ProcessingStatus.LOADED)
        self.assertEqual(uploaded_file.valid_record_count, 69239)
        # Real-data check: CBS for 2026-07-14 yields 69,239 fact rows via
        # staging (same figure as before the redesign), none skipped.
        self.assertEqual(load_result["loaded_count"], 69239)
        self.assertEqual(load_result["skipped_count"], 0)
        self.assertFalse(
            FileValidationException.objects.filter(
                uploaded_file=uploaded_file, rule_code="SKIPPED_UNLOADABLE_ROW"
            ).exists()
        )

        records = TransactionFact.objects.filter(uploaded_file=uploaded_file)
        self.assertEqual(records.count(), 69239)

        # Rows were loaded from more than just worksheet "Report" -- the
        # worksheet name is stored inside row_reference ("<worksheet>:<row
        # number>").
        worksheets_seen = {r.row_reference.split(":", 1)[0] for r in records}
        self.assertTrue(worksheets_seen & {"Report1", "Report2"})

        # Every loaded row has a genuine positive amount, and every row
        # whose remarks matched "By/To UPI/<RRN>/" got a real CREDIT/DEBIT
        # direction -- confirms remarks/amount came from the *correct*
        # per-worksheet columns. Non-UPI GL rows legitimately keep UNKNOWN.
        for record in records:
            self.assertGreater(record.amount, Decimal("0"))
            self.assertIsNotNone(record.staging_row_id)
            if record.rrn:
                self.assertRegex(record.rrn, r"^\d+$")
                self.assertIn(record.direction, (Direction.CREDIT, Direction.DEBIT))


class ResolveColumnMappingUnitTests(TestCase):
    """Unit tests for file_processing.tasks._resolve_column_mapping in
    isolation -- covers the flat (back-compat), per-worksheet, and
    "_default" fallback shapes without needing a real config/file."""

    def _config(self, column_mapping):
        class _FakeConfig:
            pass

        cfg = _FakeConfig()
        cfg.column_mapping = column_mapping
        return cfg

    def test_flat_mapping_is_returned_unchanged_regardless_of_worksheet(self):
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

        self.assertEqual(_resolve_column_mapping(config, "Report1"), {"date": 1, "remarks": 2})
        self.assertEqual(_resolve_column_mapping(config, "Report"), {"date": 1, "remarks": 3})

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


class _SyntheticFileMixin:
    def _build_config(self, payment_module, **overrides) -> FileTypeConfig:
        values = dict(
            payment_module=payment_module,
            file_type_code="RAW_DATA",
            register_type=RegisterType.RAW_DATA,
            payload_format=PayloadFormat.CSV,
            container_format=ContainerFormat.NONE,
            has_header=False,
            column_mapping={"rrn": "4", "amount": "8"},
        )
        values.update(overrides)
        return FileTypeConfig.objects.create(**values)

    def _build_uploaded_file(self, payment_module, config, sha="synthetic-hash") -> UploadedFile:
        return UploadedFile.objects.create(
            filename="synthetic.csv",
            path="synthetic.csv",
            payment_module=payment_module,
            file_type_config=config,
            business_date=date(2026, 7, 14),
            declared_format=".csv",
            size_bytes=1,
            sha256_hash=sha,
        )


class LoadToStagingUnitTests(_SyntheticFileMixin, TestCase):
    """Synthetic validate_structure_and_content-shaped contexts."""

    def _context(self, uploaded_file):
        return {
            "uploaded_file_id": uploaded_file.id,
            "halted": False,
            "stripped_count": 0,
            "rows": [
                {"row_number": 2, "worksheet": None, "cells": {"1": "TX", "4": "123456789012", "8": "50000"}},
                {"row_number": 3, "worksheet": None, "cells": {"1": "TX", "4": "123456789013", "8": None}},
            ],
        }

    def test_stages_rows_with_raw_cells_and_typed_columns_and_sets_staged(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        uploaded_file = self._build_uploaded_file(payment_module, config)

        result = load_to_staging(self._context(uploaded_file))

        self.assertFalse(result["halted"])
        self.assertEqual(result["staged_count"], 2)
        self.assertNotIn("rows", result)  # rows now live in staging, not the context
        uploaded_file.refresh_from_db()
        self.assertEqual(uploaded_file.processing_status, ProcessingStatus.STAGED)

        rows = list(StagingRawData.objects.filter(uploaded_file=uploaded_file).order_by("id"))
        self.assertEqual([r.row_reference for r in rows], [":2", ":3"])
        self.assertEqual(rows[0].raw_cells, {"1": "TX", "4": "123456789012", "8": "50000"})
        self.assertEqual(rows[0].rrn_raw, "123456789012")
        self.assertEqual(rows[0].amount_paise, "50000")
        self.assertIsNone(rows[1].amount_paise)  # empty source cell stays NULL

    def test_typed_column_left_null_when_mapping_key_missing(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module, column_mapping={"rrn": "4"})  # no "amount"
        uploaded_file = self._build_uploaded_file(payment_module, config)

        load_to_staging(self._context(uploaded_file))

        row = StagingRawData.objects.filter(uploaded_file=uploaded_file).order_by("id").first()
        self.assertEqual(row.rrn_raw, "123456789012")
        self.assertIsNone(row.amount_paise)
        self.assertEqual(row.raw_cells["8"], "50000")  # still preserved in raw_cells

    def test_retried_staging_is_idempotent_via_ignore_conflicts(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        uploaded_file = self._build_uploaded_file(payment_module, config)

        load_to_staging(self._context(uploaded_file))
        result = load_to_staging(self._context(uploaded_file))  # simulated retry

        self.assertEqual(result["staged_count"], 2)
        self.assertEqual(StagingRawData.objects.filter(uploaded_file=uploaded_file).count(), 2)

    def test_halted_context_is_a_no_op(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        uploaded_file = self._build_uploaded_file(payment_module, config)

        result = load_to_staging({"uploaded_file_id": uploaded_file.id, "halted": True, "rows": []})

        self.assertTrue(result["halted"])
        self.assertFalse(StagingRawData.objects.filter(uploaded_file=uploaded_file).exists())
        uploaded_file.refresh_from_db()
        self.assertEqual(uploaded_file.processing_status, ProcessingStatus.RECEIVED)

    def test_register_type_without_staging_table_fails_cleanly(self):
        payment_module = _build_payment_module()
        # SWITCH (no source file exists) -- NTSL was the example here until
        # it got its own staging table on 2026-09-27.
        config = self._build_config(payment_module, file_type_code="SWITCH", register_type=RegisterType.SWITCH)
        uploaded_file = self._build_uploaded_file(payment_module, config)

        result = load_to_staging(self._context(uploaded_file))

        self.assertTrue(result["halted"])
        uploaded_file.refresh_from_db()
        self.assertEqual(uploaded_file.processing_status, ProcessingStatus.FAILED)
        self.assertTrue(
            FileValidationException.objects.filter(
                uploaded_file=uploaded_file, rule_code="NO_STAGING_TABLE"
            ).exists()
        )

    def test_range_contains_pauses_after_staging_with_minimal_context(self):
        payment_module = _build_payment_module()
        config = self._build_config(
            payment_module,
            file_type_code="CBS_GL_EXTRACT",
            register_type=RegisterType.CBS_GL_EXTRACT,
            column_mapping={"date": 1, "remarks": 3, "withdrawals": 7, "deposits": 10},
            date_validation_mode=DateValidationMode.RANGE_CONTAINS,
        )
        uploaded_file = self._build_uploaded_file(payment_module, config)

        result = load_to_staging({
            "uploaded_file_id": uploaded_file.id,
            "halted": False,
            "rows": [
                {"row_number": 9, "worksheet": "Report",
                 "cells": {"1": "2026-07-14T00:00:00.000", "3": "By UPI/123456789012/x/y", "10": "5.00"}},
            ],
        })

        self.assertTrue(result["halted"])
        uploaded_file.refresh_from_db()
        self.assertEqual(uploaded_file.processing_status, ProcessingStatus.AWAITING_DATE_SELECTION)
        self.assertEqual(uploaded_file.pending_pipeline_context, {"uploaded_file_id": uploaded_file.id, "halted": True})
        row = StagingCbsGlExtract.objects.get(uploaded_file=uploaded_file)
        self.assertEqual(row.worksheet, "Report")
        self.assertEqual(row.row_reference, "Report:9")
        self.assertEqual(row.remarks, "By UPI/123456789012/x/y")
        self.assertEqual(row.deposits, "5.00")
        self.assertIsNone(row.withdrawals)
        self.assertIsNone(row.balance)  # no "balance" key in this config
        self.assertTrue(
            FileValidationException.objects.filter(
                uploaded_file=uploaded_file,
                rule_code="AWAITING_DATE_SELECTION",
                severity=ValidationSeverity.INFO,
            ).exists()
        )


class NormalizeRowsFromStagingUnitTests(_SyntheticFileMixin, TestCase):
    def test_reads_rows_from_staging_not_from_context(self):
        payment_module = _build_payment_module()
        config = self._build_config(
            payment_module, amount_source_fields=["amount"], amount_scale_factor=Decimal("0.01"),
            direction_derivation_rule="constant:DEBIT", rrn_expected_digit_length=12,
        )
        uploaded_file = self._build_uploaded_file(payment_module, config)
        staged = StagingRawData.objects.create(
            uploaded_file=uploaded_file, row_number=2, row_reference=":2",
            raw_cells={"4": "123456789012", "8": "50000"},
        )

        # Context carries no rows at all -- normalize_rows must use staging.
        result = normalize_rows({"uploaded_file_id": uploaded_file.id, "halted": False})

        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual(row["staging_row_id"], staged.id)
        self.assertEqual(row["row_reference"], ":2")
        self.assertEqual(row["rrn"], "123456789012")
        self.assertEqual(Decimal(row["amount"]), Decimal("500"))  # 50000 paise
        self.assertEqual(row["direction"], Direction.DEBIT)
        self.assertIsNone(row["txn_amount"])
        self.assertNotIn("cells", row)

    def test_halted_context_is_a_no_op(self):
        result = normalize_rows({"uploaded_file_id": 0, "halted": True})
        self.assertTrue(result["halted"])


class PromoteToFactUnitTests(_SyntheticFileMixin, TestCase):
    """Fast, non-file-backed unit tests against synthetic normalize_rows-
    shaped contexts -- skip-on-unparseable-amount, idempotent retry, and
    halted no-op."""

    def _row(self, **overrides):
        row = {
            "row_number": 2,
            "worksheet": None,
            "row_reference": ":2",
            "staging_row_id": 41,
            "rrn": "123456789012",
            "rrn_valid": True,
            "direction": Direction.UNKNOWN,
            "amount": "500.00",
            "txn_amount": None,
            "row_date": None,
        }
        row.update(overrides)
        return row

    def test_promotes_row_with_staging_lineage(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        uploaded_file = self._build_uploaded_file(payment_module, config)

        result = promote_to_fact({"uploaded_file_id": uploaded_file.id, "halted": False, "rows": [self._row()]})

        self.assertEqual(result["loaded_count"], 1)
        self.assertEqual(result["skipped_count"], 0)
        fact = TransactionFact.objects.get(uploaded_file=uploaded_file)
        self.assertEqual(fact.staging_row_id, 41)
        self.assertEqual(fact.row_reference, ":2")
        self.assertEqual(fact.amount, Decimal("500.00"))
        self.assertIsNone(fact.txn_amount)
        uploaded_file.refresh_from_db()
        self.assertEqual(uploaded_file.processing_status, ProcessingStatus.LOADED)

    def test_row_with_unparseable_amount_is_skipped_not_loaded(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        uploaded_file = self._build_uploaded_file(payment_module, config)

        result = promote_to_fact({
            "uploaded_file_id": uploaded_file.id,
            "halted": False,
            "rows": [self._row(amount=None)],  # normalize_rows already logged UNPARSEABLE_AMOUNT
        })

        self.assertEqual(result["loaded_count"], 0)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(TransactionFact.objects.filter(uploaded_file=uploaded_file).count(), 0)
        self.assertTrue(
            FileValidationException.objects.filter(
                uploaded_file=uploaded_file,
                rule_code="SKIPPED_UNLOADABLE_ROW",
                severity=ValidationSeverity.ERROR,
            ).exists()
        )

    def test_retried_promote_is_idempotent_via_ignore_conflicts(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        uploaded_file = self._build_uploaded_file(payment_module, config)
        context = {"uploaded_file_id": uploaded_file.id, "halted": False, "rows": [self._row()]}

        promote_to_fact(context)
        promote_to_fact(context)  # simulated retry, identical input

        self.assertEqual(TransactionFact.objects.filter(uploaded_file=uploaded_file).count(), 1)

    def test_halted_context_is_a_no_op(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module)
        uploaded_file = self._build_uploaded_file(payment_module, config)

        result = promote_to_fact({"uploaded_file_id": uploaded_file.id, "halted": True, "rows": []})

        self.assertTrue(result["halted"])
        self.assertEqual(TransactionFact.objects.filter(uploaded_file=uploaded_file).count(), 0)

    def test_adjustment_row_gets_txn_amount(self):
        payment_module = _build_payment_module()
        config = self._build_config(payment_module, file_type_code="ADJUSTMENT", register_type=RegisterType.ADJUSTMENT)
        uploaded_file = self._build_uploaded_file(payment_module, config)

        promote_to_fact({
            "uploaded_file_id": uploaded_file.id,
            "halted": False,
            "rows": [self._row(amount="847.75", txn_amount="896.75")],
        })

        fact = TransactionFact.objects.get(uploaded_file=uploaded_file)
        self.assertEqual(fact.amount, Decimal("847.75"))  # Adjamount
        self.assertEqual(fact.txn_amount, Decimal("896.75"))  # Txnamount


class BuildProcessingChainTests(TestCase):
    """EXACT_MATCH: all 5 stages. RANGE_CONTAINS: stops after
    load_to_staging (see admin.py's resume step for normalize + promote)."""

    def _uploaded_file(self, date_validation_mode):
        payment_module = _build_payment_module()
        config = FileTypeConfig.objects.create(
            payment_module=payment_module,
            file_type_code="DRC",
            register_type=RegisterType.DRC,
            payload_format=PayloadFormat.CSV,
            date_validation_mode=date_validation_mode,
        )
        return UploadedFile.objects.create(
            filename="x.csv",
            path="x.csv",
            payment_module=payment_module,
            file_type_config=config,
            business_date=date(2026, 7, 14),
            declared_format=".csv",
            size_bytes=1,
            sha256_hash="chain-test-hash",
        )

    def test_exact_match_chain_has_all_five_stages(self):
        from file_processing.tasks import build_processing_chain

        built = build_processing_chain(self._uploaded_file(DateValidationMode.EXACT_MATCH).id)

        self.assertEqual(
            [t.task for t in built.tasks],
            [
                "file_processing.tasks.format_sniff_and_parse",
                "file_processing.tasks.validate_structure_and_content",
                "file_processing.tasks.load_to_staging",
                "file_processing.tasks.normalize_rows",
                "file_processing.tasks.promote_to_fact",
            ],
        )

    def test_range_contains_chain_stops_after_staging(self):
        from file_processing.tasks import build_processing_chain

        built = build_processing_chain(self._uploaded_file(DateValidationMode.RANGE_CONTAINS).id)

        self.assertEqual(
            [t.task for t in built.tasks],
            [
                "file_processing.tasks.format_sniff_and_parse",
                "file_processing.tasks.validate_structure_and_content",
                "file_processing.tasks.load_to_staging",
            ],
        )
