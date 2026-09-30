"""
Tests for transactions.services.archival (staging archival, pulled forward
from Phase 3 on 2026-09-28) and its `archive_older_business_dates`
management command. Fixtures reuse test_transaction_fact.py's helpers.
"""
from __future__ import annotations

from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from file_processing.models import ProcessingStatus
from transactions.models import (
    StagingCbsGlExtract,
    StagingCbsGlExtractHistory,
    TransactionFact,
)
from transactions.services.archival import archive_older_business_dates
from transactions.tests.test_transaction_fact import (
    _build_cbs_config,
    _build_fact,
    _build_payment_module,
    _build_uploaded_file,
)

OLD = date(2026, 9, 25)
LATEST = date(2026, 9, 26)


class ArchiveOlderBusinessDatesTests(TestCase):
    def setUp(self):
        self.payment_module = _build_payment_module()
        self.config = _build_cbs_config(self.payment_module)
        self.old_file = self._file(OLD, "hash-old")
        self.latest_file = self._file(LATEST, "hash-latest")
        self.old_rows = [self._stage(self.old_file, n) for n in (1, 2, 3)]
        self.latest_rows = [self._stage(self.latest_file, n) for n in (1, 2)]

    def _file(self, business_date, sha, status=ProcessingStatus.LOADED):
        return _build_uploaded_file(
            self.payment_module, self.config,
            business_date=business_date, sha256_hash=sha, processing_status=status,
        )

    def _stage(self, uploaded_file, n):
        return StagingCbsGlExtract.objects.create(
            uploaded_file=uploaded_file,
            row_number=n,
            row_reference=f"Report:{n}",
            raw_cells={"1": "2026-09-25T00:00:00.000", "3": f"By UPI/12345678901{n}/x/y"},
            worksheet="Report",
            raw_date="2026-09-25T00:00:00.000",
            remarks=f"By UPI/12345678901{n}/x/y",
            deposits="100",
        )

    def test_older_date_moves_to_history_and_latest_stays(self):
        report = archive_older_business_dates()

        self.assertEqual(report.latest_business_date, LATEST)
        self.assertEqual(report.rows_by_table, {"StagingCbsGlExtract": {OLD: 3}})
        self.assertEqual(
            set(StagingCbsGlExtract.objects.values_list("id", flat=True)),
            {r.id for r in self.latest_rows},
        )
        self.assertEqual(StagingCbsGlExtractHistory.objects.count(), 3)

        original = self.old_rows[0]
        archived = StagingCbsGlExtractHistory.objects.get(original_id=original.id)
        for name in ("uploaded_file_id", "row_number", "row_reference", "raw_cells",
                     "created_at", "worksheet", "raw_date", "remarks", "deposits"):
            self.assertEqual(getattr(archived, name), getattr(original, name), name)
        self.assertEqual(archived.archival_reason, "superseded by business_date 2026-09-26")

    def test_dry_run_writes_nothing(self):
        report = archive_older_business_dates(dry_run=True)

        self.assertEqual(report.total_rows, 3)
        self.assertEqual(StagingCbsGlExtract.objects.count(), 5)
        self.assertEqual(StagingCbsGlExtractHistory.objects.count(), 0)

    def test_second_run_is_a_no_op(self):
        archive_older_business_dates()
        report = archive_older_business_dates()

        self.assertEqual(report.total_rows, 0)
        self.assertEqual(StagingCbsGlExtract.objects.count(), 2)
        self.assertEqual(StagingCbsGlExtractHistory.objects.count(), 3)

    def test_small_batches_move_every_row(self):
        archive_older_business_dates(batch_size=1)

        self.assertEqual(StagingCbsGlExtract.objects.count(), 2)
        self.assertEqual(StagingCbsGlExtractHistory.objects.count(), 3)

    def test_file_without_business_date_is_left_alone(self):
        paused = self._file(None, "hash-paused", status=ProcessingStatus.AWAITING_DATE_SELECTION)
        paused_row = self._stage(paused, 1)

        report = archive_older_business_dates()

        self.assertTrue(StagingCbsGlExtract.objects.filter(id=paused_row.id).exists())
        self.assertEqual(report.undated_rows_by_table, {"StagingCbsGlExtract": 1})

    def test_unloaded_newer_file_does_not_set_the_latest_date(self):
        # A newer file still mid-pipeline mustn't push the current day into history.
        self._file(date(2026, 9, 27), "hash-failed", status=ProcessingStatus.VALIDATION_FAILED)

        report = archive_older_business_dates()

        self.assertEqual(report.latest_business_date, LATEST)
        self.assertEqual(StagingCbsGlExtract.objects.count(), 2)

    def test_fact_stays_live_and_lineage_resolves_to_history(self):
        fact = _build_fact(
            self.payment_module, self.old_file,
            business_date=OLD, staging_row_id=self.old_rows[0].id,
        )

        archive_older_business_dates()

        self.assertTrue(TransactionFact.objects.filter(id=fact.id).exists())
        resolved = fact.staging_row()
        self.assertIsInstance(resolved, StagingCbsGlExtractHistory)
        self.assertEqual(resolved.original_id, self.old_rows[0].id)

    def test_command_dry_run_output(self):
        out = StringIO()
        call_command("archive_older_business_dates", "--dry-run", stdout=out)

        text = out.getvalue()
        self.assertIn("latest business date 2026-09-26 stays in staging", text)
        self.assertIn("Would archive 3 staging rows", text)
        self.assertEqual(StagingCbsGlExtractHistory.objects.count(), 0)
