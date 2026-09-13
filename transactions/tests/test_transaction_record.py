"""
Schema-level tests for `TransactionRecord` (approved plan, "Create the
TransactionRecord Schema"). This only exercises the model/constraint --
`load_to_unified_table` (the actual per-file-type population logic) is
out of scope here and remains a stub.

Fixture pattern (PaymentModule / FileTypeConfig / UploadedFile) follows
`file_processing/tests/test_date_selection.py`'s convention, but doesn't
need a real sample file on disk since these tests never run the parsing
pipeline against `uploaded_file.path` -- only the DB rows matter here.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import Direction, PaymentModule, RegisterType
from file_processing.models import FileTypeConfig, PayloadFormat, UploadedFile
from transactions.models import TransactionRecord


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
        sha256_hash="test-hash-transaction-record",
    )
    defaults.update(overrides)
    return UploadedFile.objects.create(**defaults)


def _build_transaction_record(payment_module, uploaded_file, **overrides) -> TransactionRecord:
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
    return TransactionRecord.objects.create(**defaults)


class TransactionRecordTests(TestCase):
    def setUp(self):
        self.payment_module = _build_payment_module()
        self.config = _build_cbs_config(self.payment_module)
        self.uploaded_file = _build_uploaded_file(self.payment_module, self.config)

    def test_create_transaction_record(self):
        record = _build_transaction_record(self.payment_module, self.uploaded_file)
        self.assertEqual(record.rrn, "123456789012")
        self.assertEqual(record.amount, Decimal("3500.00"))
        self.assertTrue(record.rrn_valid)
        self.assertEqual(str(record), f"{RegisterType.CBS_GL_EXTRACT}:123456789012 (2026-07-14)")

    def test_unique_row_per_uploaded_file_rejects_duplicate(self):
        _build_transaction_record(self.payment_module, self.uploaded_file, row_reference="Report1!23")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _build_transaction_record(
                    self.payment_module, self.uploaded_file, row_reference="Report1!23"
                )

    def test_same_row_reference_allowed_across_different_uploaded_files(self):
        _build_transaction_record(self.payment_module, self.uploaded_file, row_reference="Report1!23")

        other_uploaded_file = _build_uploaded_file(
            self.payment_module, self.config, sha256_hash="a-different-hash"
        )
        # Should not raise -- uniqueness is scoped per uploaded_file, not global.
        _build_transaction_record(
            self.payment_module, other_uploaded_file, row_reference="Report1!23"
        )

    def test_rrn_repeats_across_records_without_constraint_violation(self):
        _build_transaction_record(self.payment_module, self.uploaded_file, row_reference="row-1")
        # Same RRN, different row -- deliberately allowed (cross-source matching
        # is the point; there is no uniqueness constraint on rrn itself).
        _build_transaction_record(self.payment_module, self.uploaded_file, row_reference="row-2")

        self.assertEqual(
            TransactionRecord.objects.filter(rrn="123456789012").count(), 2
        )
