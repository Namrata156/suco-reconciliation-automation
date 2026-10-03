"""
Tests for the one-off `fix_cbs_rrn_leading_zeros` command (2026-10-02):
restores CBS RRN leading zeros on fact rows loaded before extraction padded
them. Fixtures reuse test_transaction_fact.py's helpers.
"""
from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from transactions.models import StagingCbsGlExtract, StagingCbsGlExtractHistory, TransactionFact
from transactions.tests.test_transaction_fact import (
    _build_cbs_config,
    _build_fact,
    _build_payment_module,
    _build_uploaded_file,
)


class FixCbsRrnLeadingZerosTests(TestCase):
    def setUp(self):
        self.payment_module = _build_payment_module()
        self.uploaded_file = _build_uploaded_file(self.payment_module, _build_cbs_config(self.payment_module))
        # Loaded before the fix: short RRN, flagged invalid.
        self.short = self._fact(1, "By UPI/4383934640/x@ybl/Payment from PhonePe", "4383934640", valid=False)
        # Archived date: the remarks live only in staging history.
        self.archived = self._fact(
            2, "To UPI/71131310561/x@ybl/Paid", "71131310561", valid=False, archive=True
        )
        # A GL entry with no RRN in its remarks stays invalid.
        self.gl_entry = self._fact(3, "NULLIFICATION OF UPI TRXN AMT TRF TO  GL7/1526", "", valid=False)
        self.already_valid = self._fact(4, "By UPI/315485225633/x@ybl/y", "315485225633", valid=True)

    def _fact(self, n, remarks, rrn, valid, archive=False):
        staging = StagingCbsGlExtract.objects.create(
            uploaded_file=self.uploaded_file,
            row_number=n,
            row_reference=f"Report:{n}",
            raw_cells={"3": remarks},
            worksheet="Report",
            remarks=remarks,
        )
        staging_row_id = staging.id
        if archive:
            StagingCbsGlExtractHistory.objects.create(
                original_id=staging.id,
                uploaded_file=self.uploaded_file,
                row_number=n,
                row_reference=staging.row_reference,
                raw_cells=staging.raw_cells,
                created_at=staging.created_at,
                worksheet="Report",
                remarks=remarks,
            )
            staging.delete()
        return _build_fact(
            self.payment_module,
            self.uploaded_file,
            rrn=rrn,
            rrn_valid=valid,
            row_reference=f"Report:{n}",
            staging_row_id=staging_row_id,
        )

    def _run(self, *args):
        out = StringIO()
        call_command("fix_cbs_rrn_leading_zeros", *args, stdout=out)
        return out.getvalue()

    def _state(self):
        return dict(TransactionFact.objects.values_list("id", "rrn")), dict(
            TransactionFact.objects.values_list("id", "rrn_valid")
        )

    def test_dry_run_changes_nothing(self):
        before = self._state()
        output = self._run("--dry-run")
        self.assertIn("Would fix 2 CBS RRNs", output)
        self.assertEqual(self._state(), before)

    def test_restores_leading_zeros_from_staging_and_history(self):
        output = self._run()
        self.assertIn("Fixed 2 CBS RRNs", output)

        for fact, rrn, valid in (
            (self.short, "004383934640", True),
            (self.archived, "071131310561", True),
            (self.gl_entry, "", False),
            (self.already_valid, "315485225633", True),
        ):
            fact.refresh_from_db()
            self.assertEqual((fact.rrn, fact.rrn_valid), (rrn, valid))
        self.assertEqual(TransactionFact.objects.count(), 4)

    def test_second_run_changes_nothing(self):
        self._run()
        self.assertIn("Fixed 0 CBS RRNs", self._run())
