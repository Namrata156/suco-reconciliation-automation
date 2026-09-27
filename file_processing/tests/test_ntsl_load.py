"""
NTSL loading (added 2026-09-27): `format_sniff_and_parse` ->
`validate_structure_and_content` -> `load_to_staging` (StagingNtsl, every
parsed row kept) -> `promote_to_settlement_summary` (NtslSettlementLine).
NTSL never reaches normalize_rows / promote_to_fact (no RRN -- see
doubts.md "NTSL -- not an RRN-level register").

Runs the stage functions directly against the real files in
`14072026/NTSL/` (same direct-call pattern as test_staging_and_fact.py -- no
Celery broker). FileTypeConfig / SettlementCycle fixtures are built locally,
mirroring the seeded NTSL config, per this codebase's `_build_*_config()`
convention.
"""
from __future__ import annotations

import hashlib
from datetime import date, time
from decimal import Decimal
from pathlib import Path

from django.test import TestCase

from core.models import PaymentModule, RegisterType, SettlementCycle, SettlementCycleType
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
from file_processing.parsers.ntsl_ooxml import parse_ooxml_workbook
from file_processing.tasks import (
    build_processing_chain,
    format_sniff_and_parse,
    load_to_staging,
    promote_to_settlement_summary,
    validate_structure_and_content,
)
from transactions.models import NtslSettlementLine, StagingNtsl, TransactionFact

NTSL_ROOT = Path(__file__).resolve().parents[2] / "14072026" / "NTSL"
NTSL_1C = NTSL_ROOT / "UPI_NTSLSSK140726_1C.xls"
NTSL_DC1 = NTSL_ROOT / "UPI_NTSLSSK140726_DC1.xls"


def _build_payment_module() -> PaymentModule:
    return PaymentModule.objects.create(code="UPI", name="UPI")


def _build_cycle(payment_module, code, cycle_type=SettlementCycleType.AUTH) -> SettlementCycle:
    return SettlementCycle.objects.create(
        payment_module=payment_module,
        cycle_code=code,
        cycle_type=cycle_type,
        start_time=time(0, 0),
        end_time=time(0, 0),
    )


def _build_ntsl_config(payment_module) -> FileTypeConfig:
    return FileTypeConfig.objects.create(
        payment_module=payment_module,
        file_type_code="NTSL",
        register_type=RegisterType.NTSL,
        payload_format=PayloadFormat.OOXML_ZIP,
        container_format=ContainerFormat.NONE,
        has_header=False,
        column_mapping={"description": 1, "txn_count": 2, "debit": 3, "credit": 4},
        required_columns=[],
        amount_source_fields=[],
        amount_scale_factor=Decimal("1"),
        pagination_artifact_patterns=[],
        date_validation_mode=DateValidationMode.EXACT_MATCH,
        is_cycle_wise=True,
        cycle_set=[*(f"{n}C" for n in range(1, 11)), "DC1", "DC2"],
        outer_filename_pattern=r"^UPI_NTSLSSK\d{6}_(?:(?:[1-9]|10)C|DC[12])\.xls$",
    )


def _build_uploaded_file(payment_module, config, path: Path, cycle) -> UploadedFile:
    return UploadedFile.objects.create(
        filename=path.name,
        path=str(path),
        payment_module=payment_module,
        file_type_config=config,
        business_date=date(2026, 7, 14),
        settlement_cycle=cycle,
        declared_format=path.suffix,
        size_bytes=path.stat().st_size,
        sha256_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _run_ntsl_pipeline(uploaded_file: UploadedFile) -> dict:
    ctx = format_sniff_and_parse(uploaded_file.id)
    ctx = validate_structure_and_content(ctx)
    ctx = load_to_staging(ctx)
    return promote_to_settlement_summary(ctx)


def _is_number(raw) -> bool:
    try:
        return raw is not None and str(raw).strip() != "" and Decimal(str(raw).strip()).is_finite()
    except Exception:  # noqa: BLE001
        return False


def _expected_category_row_count(path: Path) -> int:
    """Independent re-derivation of the category-line rule from the raw
    parser output: non-empty description AND >=1 numeric cell in cols 2-4."""
    return sum(
        1
        for row in parse_ooxml_workbook(path)
        if (row.cells.get(1) or "").strip() and any(_is_number(row.cells.get(c)) for c in (2, 3, 4))
    )


class Ntsl1CLoadTests(TestCase):
    def setUp(self):
        self.payment_module = _build_payment_module()
        self.config = _build_ntsl_config(self.payment_module)
        self.cycle_1c = _build_cycle(self.payment_module, "1C")
        self.uploaded_file = _build_uploaded_file(self.payment_module, self.config, NTSL_1C, self.cycle_1c)

    def test_real_1c_file_stages_every_row_and_loads_category_lines(self):
        parsed_count = len(list(parse_ooxml_workbook(NTSL_1C)))
        self.assertEqual(parsed_count, 500)  # what the parser actually yields for this file

        result = _run_ntsl_pipeline(self.uploaded_file)

        self.assertFalse(result["halted"])
        self.uploaded_file.refresh_from_db()
        self.assertEqual(self.uploaded_file.processing_status, ProcessingStatus.LOADED)

        # Staging keeps every parsed row (titles, header, blanks included).
        self.assertEqual(StagingNtsl.objects.filter(uploaded_file=self.uploaded_file).count(), parsed_count)

        lines = NtslSettlementLine.objects.filter(uploaded_file=self.uploaded_file)
        expected_lines = _expected_category_row_count(NTSL_1C)
        self.assertEqual(expected_lines, 164)
        self.assertEqual(lines.count(), expected_lines)
        self.assertEqual(result["loaded_count"], expected_lines)
        self.assertEqual(self.uploaded_file.valid_record_count, expected_lines)

        # Title / header rows are never loaded.
        self.assertFalse(lines.filter(description="Description").exists())
        self.assertFalse(lines.filter(description__startswith="Daily Settlement Statement").exists())
        self.assertFalse(lines.filter(description="FOR NON - FINANCIAL MANDATE/OD TXNs").exists())

        final = lines.get(is_final_settlement=True)
        self.assertEqual(final.description, "Final Settlement Amount")
        self.assertEqual(final.debit, Decimal("507341.89"))
        self.assertEqual(final.credit, Decimal("0"))
        self.assertIsNone(final.txn_count)  # blank in the source
        self.assertEqual(final.settlement_cycle, self.cycle_1c)
        self.assertEqual(final.business_date, date(2026, 7, 14))
        self.assertEqual(final.staging_row().description_raw, "Final Settlement Amount")

        # txn_count "18.0" -> 18 (row 6 of the real file).
        first = lines.get(description="Remitter Default-BBPS U2 Approved Fee")
        self.assertEqual(first.txn_count, 18)
        self.assertIsInstance(first.txn_count, int)

        # The real file's two numeric rows with an empty description (rows 160
        # and 175) are skipped with ONE INFO exception carrying the count.
        exceptions = FileValidationException.objects.filter(uploaded_file=self.uploaded_file)
        empty_desc = exceptions.filter(rule_code="NTSL_EMPTY_DESCRIPTION_ROWS_SKIPPED")
        self.assertEqual(empty_desc.count(), 1)
        self.assertEqual(empty_desc.get().severity, ValidationSeverity.INFO)
        self.assertTrue(empty_desc.get().message.startswith("2 row(s)"))

        # Header agrees with business_date/cycle; nothing else is wrong.
        self.assertFalse(exceptions.filter(severity=ValidationSeverity.WARNING).exists())
        self.assertFalse(exceptions.filter(severity=ValidationSeverity.ERROR).exists())

        # NTSL never produces TransactionFact rows.
        self.assertFalse(TransactionFact.objects.filter(uploaded_file=self.uploaded_file).exists())

    def test_rerunning_promote_is_idempotent(self):
        _run_ntsl_pipeline(self.uploaded_file)
        first_count = NtslSettlementLine.objects.filter(uploaded_file=self.uploaded_file).count()

        result = promote_to_settlement_summary({"uploaded_file_id": self.uploaded_file.id, "halted": False})

        self.assertEqual(NtslSettlementLine.objects.filter(uploaded_file=self.uploaded_file).count(), first_count)
        self.assertEqual(result["loaded_count"], first_count)
        self.assertEqual(
            NtslSettlementLine.objects.filter(uploaded_file=self.uploaded_file, is_final_settlement=True).count(), 1
        )

    def test_halted_context_is_a_no_op(self):
        result = promote_to_settlement_summary({"uploaded_file_id": self.uploaded_file.id, "halted": True})

        self.assertTrue(result["halted"])
        self.assertFalse(NtslSettlementLine.objects.exists())

    def test_chain_is_the_four_ntsl_stages(self):
        built = build_processing_chain(self.uploaded_file.id)

        self.assertEqual(
            [t.task for t in built.tasks],
            [
                "file_processing.tasks.format_sniff_and_parse",
                "file_processing.tasks.validate_structure_and_content",
                "file_processing.tasks.load_to_staging",
                "file_processing.tasks.promote_to_settlement_summary",
            ],
        )


class NtslHeaderMismatchTests(TestCase):
    def test_1c_file_tagged_as_2c_records_header_warning_but_still_loads(self):
        payment_module = _build_payment_module()
        config = _build_ntsl_config(payment_module)
        cycle_2c = _build_cycle(payment_module, "2C")
        uploaded_file = _build_uploaded_file(payment_module, config, NTSL_1C, cycle_2c)

        _run_ntsl_pipeline(uploaded_file)

        uploaded_file.refresh_from_db()
        self.assertEqual(uploaded_file.processing_status, ProcessingStatus.LOADED)
        warning = FileValidationException.objects.get(
            uploaded_file=uploaded_file, rule_code="NTSL_HEADER_MISMATCH"
        )
        self.assertEqual(warning.severity, ValidationSeverity.WARNING)
        self.assertIn("cycle 1C vs settlement_cycle 2C", warning.message)
        self.assertNotIn("vs business_date", warning.message)  # date still agrees
        self.assertEqual(
            NtslSettlementLine.objects.filter(uploaded_file=uploaded_file).count(),
            _expected_category_row_count(NTSL_1C),
        )


class NtslDc1LoadTests(TestCase):
    def test_real_dc1_file_skips_heading_row_and_loads_final_credit(self):
        payment_module = _build_payment_module()
        config = _build_ntsl_config(payment_module)
        cycle_dc1 = _build_cycle(payment_module, "DC1", SettlementCycleType.DISPUTE)
        uploaded_file = _build_uploaded_file(payment_module, config, NTSL_DC1, cycle_dc1)

        _run_ntsl_pipeline(uploaded_file)

        uploaded_file.refresh_from_db()
        self.assertEqual(uploaded_file.processing_status, ProcessingStatus.LOADED)
        lines = NtslSettlementLine.objects.filter(uploaded_file=uploaded_file)
        self.assertEqual(lines.count(), _expected_category_row_count(NTSL_DC1))

        # Heading row is staged (raw) but never loaded as a line.
        self.assertTrue(
            StagingNtsl.objects.filter(
                uploaded_file=uploaded_file, description_raw="FOR NON - FINANCIAL MANDATE/OD TXNs"
            ).exists()
        )
        self.assertFalse(lines.filter(description__icontains="NON - FINANCIAL MANDATE").exists())

        final = lines.get(is_final_settlement=True)
        self.assertEqual(final.debit, Decimal("0"))
        self.assertEqual(final.credit, Decimal("435.00"))
        self.assertFalse(
            FileValidationException.objects.filter(
                uploaded_file=uploaded_file, severity=ValidationSeverity.WARNING
            ).exists()
        )
