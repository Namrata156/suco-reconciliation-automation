"""
Exercises the interactive business-date selection flow for RANGE_CONTAINS
(CBS GL Extract) files: validate_structure_and_content detects the file's
actual distinct row dates, load_to_staging stages every row and then pauses
at AWAITING_DATE_SELECTION (rather than requiring business_date to be
guessed up front), and the admin "Select business date" action (the
Phase-1 Django-admin stand-in for the real Phase 5 upload API/UI -- see
tracker.md) should resume the pipeline by re-queuing normalize_rows (which
reads the staged rows back) once a date is chosen. Since the 2026-09-23
staging redesign, the paused context is only {"uploaded_file_id", "halted"}.

The first test class runs the real pipeline functions directly (same
direct-call pattern used against this file in test_cbs_parser.py) against
the real 154,661-row sample `14072026/cbs_report/b5rg090oa.xls`, confirmed
"From 13/07/2026 To 15/07/2026". The second class is a lighter-weight
admin-level smoke test using Django's test Client against the actual
admin.py code path, with a stub `pending_pipeline_context` and Celery's `chain`/`apply_async`
mocked out so it doesn't require a live broker.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from core.models import PaymentModule, RegisterType
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
    format_sniff_and_parse,
    load_to_staging,
    normalize_rows,
    validate_structure_and_content,
)
from file_processing.tests.test_pagination_strip import CBS_ARTIFACT_PATTERNS
from transactions.models import StagingCbsGlExtract

# Formerly 14072026/cbs_report/b5rg090oa.xls -- same file (same SHA-256), renamed.
SAMPLE_ROOT = Path(__file__).resolve().parents[2] / "sample_decrypted_files" / "14072026"
CBS_SAMPLE = SAMPLE_ROOT / "cbs_14072026.xls"

# CBS_ARTIFACT_PATTERNS is imported from test_pagination_strip (the
# canonical reference list for CBS's FileTypeConfig.pagination_artifact_
# patterns) rather than kept as a locally-patched copy here -- see that
# module for the full audit notes on what these patterns cover.


def _build_payment_module() -> PaymentModule:
    return PaymentModule.objects.create(code="UPI", name="UPI")


def _build_cbs_config(payment_module: PaymentModule) -> FileTypeConfig:
    return FileTypeConfig.objects.create(
        payment_module=payment_module,
        file_type_code="CBS_GL_EXTRACT",
        register_type=RegisterType.CBS_GL_EXTRACT,
        payload_format=PayloadFormat.SPREADSHEETML_XML,
        container_format=ContainerFormat.NONE,
        column_mapping={"date": 1, "remarks": 3, "withdrawals": 7, "deposits": 10},
        required_columns=["date"],
        amount_source_fields=["withdrawals", "deposits"],
        direction_derivation_rule="remarks_prefix:By=CREDIT,To=DEBIT",
        rrn_expected_digit_length=12,
        pagination_artifact_patterns=CBS_ARTIFACT_PATTERNS,
        date_validation_mode=DateValidationMode.RANGE_CONTAINS,
    )


def _build_uploaded_file(payment_module: PaymentModule, config: FileTypeConfig, **overrides) -> UploadedFile:
    defaults = dict(
        filename="b5rg090oa.xls",
        path=str(CBS_SAMPLE),
        payment_module=payment_module,
        file_type_config=config,
        business_date=None,
        declared_format=".xls",
        size_bytes=CBS_SAMPLE.stat().st_size,
        sha256_hash="test-hash",
    )
    defaults.update(overrides)
    return UploadedFile.objects.create(**defaults)


class CbsDateSelectionPipelineTests(TestCase):
    """Runs the real stage-2/stage-3/stage-4 functions directly against the
    real sample file -- no Celery broker involved (calling a `@shared_task`
    function directly invokes it synchronously, bypassing messaging
    entirely)."""

    def test_staging_then_pauses_awaiting_date_selection(self):
        payment_module = _build_payment_module()
        config = _build_cbs_config(payment_module)
        uploaded_file = _build_uploaded_file(payment_module, config)

        parse_context = format_sniff_and_parse(uploaded_file.id)
        self.assertFalse(parse_context["halted"])

        # validate detects the dates but no longer pauses itself.
        validate_context = validate_structure_and_content(parse_context)
        self.assertFalse(validate_context["halted"])
        uploaded_file.refresh_from_db()
        self.assertEqual(uploaded_file.processing_status, ProcessingStatus.VALIDATED)
        self.assertEqual(
            uploaded_file.detected_dates,
            ["2026-07-13", "2026-07-14", "2026-07-15"],
        )

        # load_to_staging stages the rows, THEN pauses.
        result_context = load_to_staging(validate_context)
        self.assertTrue(result_context["halted"])

        uploaded_file.refresh_from_db()
        self.assertEqual(uploaded_file.processing_status, ProcessingStatus.AWAITING_DATE_SELECTION)
        self.assertEqual(
            uploaded_file.pending_pipeline_context,
            {"uploaded_file_id": uploaded_file.id, "halted": True},
        )
        self.assertGreater(StagingCbsGlExtract.objects.filter(uploaded_file=uploaded_file).count(), 0)

        info_exceptions = FileValidationException.objects.filter(
            uploaded_file=uploaded_file, severity=ValidationSeverity.INFO
        )
        self.assertTrue(info_exceptions.exists())

    def test_resume_after_date_selection_normalizes_only_selected_dates_rows(self):
        payment_module = _build_payment_module()
        config = _build_cbs_config(payment_module)
        uploaded_file = _build_uploaded_file(payment_module, config)

        parse_context = format_sniff_and_parse(uploaded_file.id)
        load_to_staging(validate_structure_and_content(parse_context))
        uploaded_file.refresh_from_db()

        # Simulate the admin action's resume step directly (see
        # UploadedFileAdmin.select_business_date in admin.py for the real
        # equivalent, exercised end-to-end in the test class below).
        uploaded_file.business_date = date(2026, 7, 14)
        uploaded_file.processing_status = ProcessingStatus.STAGED
        uploaded_file.save(update_fields=["business_date", "processing_status"])

        normalize_context = normalize_rows({"uploaded_file_id": uploaded_file.id, "halted": False})
        self.assertFalse(normalize_context["halted"])

        uploaded_file.refresh_from_db()
        self.assertEqual(uploaded_file.processing_status, ProcessingStatus.NORMALIZED)
        # Confirmed directly by running this pipeline against this sample:
        # 69,239 rows carry the 14th's date after pagination stripping and
        # ss:Index-aware (not positional) date-cell extraction. (This was
        # 69,240 before the report-end "Summation of Debits and Credit..."
        # footer row -- worksheet Report2, row 23590 -- was added to
        # CBS_ARTIFACT_PATTERNS: that row has no date cell, so previously it
        # slipped past both pagination stripping and RANGE_CONTAINS date
        # filtering and was silently counted as normalized regardless of
        # the selected business date. Any row whose own row_date is
        # unresolvable (e.g. a sparse row missing the date cell) is still
        # not filtered out by date at all -- a pre-existing normalize_rows
        # gap this feature deliberately leaves unchanged (see the approved
        # plan's "Out of scope, unchanged" note) -- so only rows with a
        # *resolved* row_date are asserted to be the 14th.
        self.assertEqual(uploaded_file.valid_record_count, 69239)
        resolved_dates = {row["row_date"] for row in normalize_context["rows"] if row["row_date"]}
        self.assertEqual(resolved_dates, {"2026-07-14"})


class SelectBusinessDateAdminActionTests(TestCase):
    """Admin-level smoke test exercising the actual admin.py code path (the
    Phase-1 Django-admin stand-in for the real Phase 5 upload API/UI) --
    not just the task functions directly. Uses a stub (post-redesign shaped)
    `pending_pipeline_context`, and mocks `chain`/`apply_async` so this doesn't require a live
    Celery broker."""

    def setUp(self):
        self.payment_module = _build_payment_module()
        self.config = _build_cbs_config(self.payment_module)
        self.uploaded_file = _build_uploaded_file(
            self.payment_module,
            self.config,
            processing_status=ProcessingStatus.AWAITING_DATE_SELECTION,
            detected_dates=["2026-07-13", "2026-07-14", "2026-07-15"],
        )
        self.uploaded_file.pending_pipeline_context = {
            "uploaded_file_id": self.uploaded_file.id,
            "halted": True,
        }
        self.uploaded_file.save(update_fields=["pending_pipeline_context"])

        admin_user = get_user_model().objects.create_superuser(
            username="admin_test", email="admin_test@example.invalid", password="test-pass-123"
        )
        self.client = Client()
        self.client.force_login(admin_user)

    def test_action_renders_intermediate_page_then_applies_selected_date(self):
        changelist_url = reverse("admin:file_processing_uploadedfile_changelist")

        intermediate_response = self.client.post(
            changelist_url,
            data={
                "action": "select_business_date",
                "_selected_action": [str(self.uploaded_file.pk)],
            },
        )
        self.assertEqual(intermediate_response.status_code, 200)
        self.assertIn(b"2026-07-14", intermediate_response.content)

        with mock.patch("file_processing.admin.chain") as mock_chain:
            apply_response = self.client.post(
                changelist_url,
                data={
                    "action": "select_business_date",
                    "_selected_action": [str(self.uploaded_file.pk)],
                    "apply": "1",
                    "business_date": "2026-07-14",
                },
            )

        self.assertEqual(apply_response.status_code, 302)
        mock_chain.assert_called_once()
        # Resume chain = normalize_rows(file id only) -> promote_to_fact.
        normalize_sig, promote_sig = mock_chain.call_args.args
        self.assertEqual(normalize_sig.task, "file_processing.tasks.normalize_rows")
        self.assertEqual(
            normalize_sig.args, ({"uploaded_file_id": self.uploaded_file.id, "halted": False},)
        )
        self.assertEqual(promote_sig.task, "file_processing.tasks.promote_to_fact")

        self.uploaded_file.refresh_from_db()
        self.assertEqual(self.uploaded_file.business_date, date(2026, 7, 14))
        # Rows were staged before the pause, so after date selection the
        # file is back at STAGED, awaiting normalize_rows.
        self.assertEqual(self.uploaded_file.processing_status, ProcessingStatus.STAGED)

    def test_action_rejects_date_not_in_detected_dates(self):
        changelist_url = reverse("admin:file_processing_uploadedfile_changelist")

        with mock.patch("file_processing.admin.chain") as mock_chain:
            response = self.client.post(
                changelist_url,
                data={
                    "action": "select_business_date",
                    "_selected_action": [str(self.uploaded_file.pk)],
                    "apply": "1",
                    "business_date": "2026-07-16",
                },
            )

        self.assertEqual(response.status_code, 302)
        mock_chain.assert_not_called()

        self.uploaded_file.refresh_from_db()
        self.assertIsNone(self.uploaded_file.business_date)
        self.assertEqual(self.uploaded_file.processing_status, ProcessingStatus.AWAITING_DATE_SELECTION)
