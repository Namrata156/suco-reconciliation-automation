"""
Model-level constraint tests for `file_processing` that aren't tied to a
specific parser/pipeline stage. See `test_date_selection.py` for the
fixture-building convention this follows.

Covers `UploadedFile`'s file-level duplicate-upload guard (approved plan,
"Create the TransactionRecord Schema" §3): the same file content
(`sha256_hash`) cannot be uploaded twice as a genuine new upload, but a
deliberate reprocess with the same hash is allowed.
"""
from __future__ import annotations

from datetime import date

from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import PaymentModule, RegisterType
from file_processing.models import FileTypeConfig, PayloadFormat, UploadedFile


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
        sha256_hash="duplicate-hash",
    )
    defaults.update(overrides)
    return UploadedFile.objects.create(**defaults)


class UploadedFileDuplicateHashConstraintTests(TestCase):
    def setUp(self):
        self.payment_module = _build_payment_module()
        self.config = _build_cbs_config(self.payment_module)

    def test_second_non_reprocess_upload_with_same_hash_is_rejected(self):
        _build_uploaded_file(self.payment_module, self.config, is_reprocess=False)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _build_uploaded_file(self.payment_module, self.config, is_reprocess=False)

    def test_genuine_reprocess_with_same_hash_is_allowed(self):
        original = _build_uploaded_file(self.payment_module, self.config, is_reprocess=False)

        # Should not raise -- a deliberate reprocess is exempt from the guard.
        reprocessed = _build_uploaded_file(
            self.payment_module,
            self.config,
            is_reprocess=True,
            reprocess_of=original,
            reprocess_reason="Corrected source file re-submitted",
        )
        self.assertEqual(reprocessed.sha256_hash, original.sha256_hash)

    def test_different_hash_is_always_allowed(self):
        _build_uploaded_file(self.payment_module, self.config, sha256_hash="hash-one")
        # Should not raise -- different content, no conflict.
        _build_uploaded_file(self.payment_module, self.config, sha256_hash="hash-two")
