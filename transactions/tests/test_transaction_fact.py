"""
Schema-level tests for the `transactions` app's staging / fact / history
tables (schema redesign approved 2026-09-23; adapted from the former
test_transaction_record.py). Only models/constraints are exercised here --
pipeline population is covered by
file_processing/tests/test_staging_and_fact.py.

Fixture pattern (PaymentModule / FileTypeConfig / UploadedFile) follows
`file_processing/tests/test_date_selection.py`'s convention, but doesn't
need a real sample file on disk.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.apps import apps
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import Direction, PaymentModule, RegisterType
from file_processing.models import FileTypeConfig, PayloadFormat, UploadedFile
from transactions.models import (
    STAGING_MODEL_BY_REGISTER_TYPE,
    StagingCbsGlExtract,
    StagingCbsGlExtractHistory,
    TransactionFact,
    TransactionFactHistory,
)


def _build_payment_module() -> PaymentModule:
    return PaymentModule.objects.create(code="UPI", name="UPI")


def _build_cbs_config(payment_module: PaymentModule) -> FileTypeConfig:
    return FileTypeConfig.objects.create(
        payment_module=payment_module,
        file_type_code="CBS_GL_EXTRACT",
        register_type=RegisterType.CBS_GL_EXTRACT,
        payload_format=PayloadFormat.SPREADSHEETML_XML,
    )


def _build_uploaded_file(payment_module: PaymentModule, config: FileTypeConfig, **overrides) -> UploadedFile:
    defaults = dict(
        filename="b5rg090oa.xls",
        path="14072026/cbs_report/b5rg090oa.xls",
        payment_module=payment_module,
        file_type_config=config,
        business_date=date(2026, 7, 14),
        declared_format=".xls",
        size_bytes=1024,
        sha256_hash="test-hash-transaction-fact",
    )
    defaults.update(overrides)
    return UploadedFile.objects.create(**defaults)


def _build_fact(payment_module, uploaded_file, **overrides) -> TransactionFact:
    defaults = dict(
        payment_module=payment_module,
        source_register_type=RegisterType.CBS_GL_EXTRACT,
        uploaded_file=uploaded_file,
        business_date=date(2026, 7, 14),
        rrn="123456789012",
        rrn_valid=True,
        amount=Decimal("3500.00"),
        direction=Direction.CREDIT,
        row_reference="Report1!23",
    )
    defaults.update(overrides)
    return TransactionFact.objects.create(**defaults)


class TransactionFactTests(TestCase):
    def setUp(self):
        self.payment_module = _build_payment_module()
        self.config = _build_cbs_config(self.payment_module)
        self.uploaded_file = _build_uploaded_file(self.payment_module, self.config)

    def test_create_transaction_fact(self):
        record = _build_fact(self.payment_module, self.uploaded_file)
        self.assertEqual(record.rrn, "123456789012")
        self.assertEqual(record.amount, Decimal("3500.00"))
        self.assertTrue(record.rrn_valid)
        self.assertIsNone(record.txn_amount)
        self.assertIsNone(record.staging_row_id)
        self.assertEqual(str(record), f"{RegisterType.CBS_GL_EXTRACT}:123456789012 (2026-07-14)")

    def test_extra_fields_removed(self):
        self.assertNotIn("extra_fields", {f.name for f in TransactionFact._meta.get_fields()})

    def test_unique_row_per_uploaded_file_rejects_duplicate(self):
        _build_fact(self.payment_module, self.uploaded_file, row_reference="Report1!23")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _build_fact(self.payment_module, self.uploaded_file, row_reference="Report1!23")

    def test_same_row_reference_allowed_across_different_uploaded_files(self):
        _build_fact(self.payment_module, self.uploaded_file, row_reference="Report1!23")

        other_uploaded_file = _build_uploaded_file(
            self.payment_module, self.config, sha256_hash="a-different-hash"
        )
        # Should not raise -- uniqueness is scoped per uploaded_file, not global.
        _build_fact(self.payment_module, other_uploaded_file, row_reference="Report1!23")

    def test_rrn_repeats_across_records_without_constraint_violation(self):
        _build_fact(self.payment_module, self.uploaded_file, row_reference="row-1")
        # Same RRN, different row -- deliberately allowed (cross-source matching
        # is the point; there is no uniqueness constraint on rrn itself).
        _build_fact(self.payment_module, self.uploaded_file, row_reference="row-2")

        self.assertEqual(TransactionFact.objects.filter(rrn="123456789012").count(), 2)

    def test_staging_row_resolves_through_register_type_map(self):
        staged = StagingCbsGlExtract.objects.create(
            uploaded_file=self.uploaded_file,
            row_number=23,
            row_reference="Report1:23",
            raw_cells={"2": "By UPI/123456789012/x/y"},
            worksheet="Report1",
            remarks="By UPI/123456789012/x/y",
        )
        fact = _build_fact(self.payment_module, self.uploaded_file, staging_row_id=staged.id)

        self.assertEqual(fact.staging_row(), staged)
        # A register type with no staging table (e.g. SWITCH) resolves to None.
        switch_fact = _build_fact(
            self.payment_module, self.uploaded_file, row_reference="x",
            source_register_type=RegisterType.SWITCH, staging_row_id=staged.id,
        )
        self.assertIsNone(switch_fact.staging_row())


class StagingSchemaTests(TestCase):
    def setUp(self):
        self.payment_module = _build_payment_module()
        self.config = _build_cbs_config(self.payment_module)
        self.uploaded_file = _build_uploaded_file(self.payment_module, self.config)

    def test_staging_map_covers_exactly_the_eight_in_scope_register_types(self):
        # NTSL added 2026-09-27 (staged, then promoted to NtslSettlementLine,
        # not TransactionFact). SWITCH still has no staging table.
        self.assertEqual(
            set(STAGING_MODEL_BY_REGISTER_TYPE),
            {
                RegisterType.CBS_GL_EXTRACT,
                RegisterType.RAW_DATA,
                RegisterType.MERCHANT_RAW_DATA,
                RegisterType.MERCHANT_RAW_DATA_BBP,
                RegisterType.DRC,
                RegisterType.TIMEOUT,
                RegisterType.ADJUSTMENT,
                RegisterType.NTSL,
            },
        )
        self.assertNotIn(RegisterType.SWITCH, STAGING_MODEL_BY_REGISTER_TYPE)

    def test_every_model_is_registered_with_the_app(self):
        """Guards the models/__init__.py re-export requirement: a model
        missing from it would be silently skipped by makemigrations."""
        names = {m.__name__ for m in apps.get_app_config("transactions").get_models()}
        expected_staging = {
            "StagingCbsGlExtract", "StagingRawData", "StagingMerchantRawData",
            "StagingMerchantRawDataBbp", "StagingDrc", "StagingTimeout", "StagingAdjustment",
            "StagingNtsl",
        }
        self.assertEqual(
            names,
            expected_staging
            | {f"{n}History" for n in expected_staging}
            | {"TransactionFact", "TransactionFactHistory"}
            | {"NtslSettlementLine", "NtslSettlementLineHistory"},
        )

    def test_staging_unique_row_per_uploaded_file(self):
        StagingCbsGlExtract.objects.create(
            uploaded_file=self.uploaded_file, row_number=1, row_reference="Report:1", raw_cells={}
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StagingCbsGlExtract.objects.create(
                    uploaded_file=self.uploaded_file, row_number=1, row_reference="Report:1", raw_cells={}
                )


class HistorySchemaTests(TestCase):
    """History tables must NOT carry the live tables' unique constraints, so
    the same logical row can be archived more than once. The archival job
    itself is covered by test_archival.py."""

    def setUp(self):
        self.payment_module = _build_payment_module()
        self.config = _build_cbs_config(self.payment_module)
        self.uploaded_file = _build_uploaded_file(self.payment_module, self.config)

    def test_same_fact_can_be_archived_twice(self):
        now = timezone.now()
        common = dict(
            original_id=7,
            payment_module=self.payment_module,
            uploaded_file=self.uploaded_file,
            source_register_type=RegisterType.CBS_GL_EXTRACT,
            business_date=date(2026, 7, 14),
            rrn="123456789012",
            rrn_valid=True,
            amount=Decimal("1.00"),
            direction=Direction.DEBIT,
            row_reference="Report:1",
            created_at=now,
            updated_at=now,
        )
        TransactionFactHistory.objects.create(archival_reason="first", **common)
        TransactionFactHistory.objects.create(archival_reason="second", **common)
        self.assertEqual(TransactionFactHistory.objects.filter(original_id=7).count(), 2)

    def test_same_staging_row_can_be_archived_twice(self):
        now = timezone.now()
        for reason in ("first", "second"):
            StagingCbsGlExtractHistory.objects.create(
                original_id=3,
                uploaded_file=self.uploaded_file,
                row_number=1,
                row_reference="Report:1",
                raw_cells={},
                created_at=now,
                archival_reason=reason,
            )
        self.assertEqual(StagingCbsGlExtractHistory.objects.filter(original_id=3).count(), 2)
