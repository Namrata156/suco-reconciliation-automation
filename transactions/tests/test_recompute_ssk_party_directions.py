"""
Tests for the one-off `recompute_ssk_party_directions` command (2026-10-08):
recomputes DRC/Timeout fact directions loaded under the old whole-file
`constant:` rule, reading party cells from staging or staging history.
Fixtures reuse test_transaction_fact.py's helpers.
"""
from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from core.models import Direction, RegisterType
from file_processing.management.commands.seed_file_type_configs import (
    ADJUSTMENT_SSK_PARTY_RULE,
    DISPUTE_SSK_PARTY_RULE,
)
from file_processing.models import FileTypeConfig, PayloadFormat
from transactions.models import (
    StagingAdjustment,
    StagingDrc,
    StagingDrcHistory,
    StagingRawData,
    StagingTimeout,
    TransactionFact,
)
from transactions.tests.test_transaction_fact import _build_fact, _build_payment_module, _build_uploaded_file


class RecomputeSskPartyDirectionsTests(TestCase):
    def setUp(self):
        self.payment_module = _build_payment_module()
        drc_file = self._file("DRC", DISPUTE_SSK_PARTY_RULE)
        timeout_file = self._file("TIMEOUT", DISPUTE_SSK_PARTY_RULE)
        adjustment_file = self._file("ADJUSTMENT", ADJUSTMENT_SSK_PARTY_RULE)
        # A file type still on a constant: rule (stands in for any non-ssk_party config).
        constant_file = self._file("RAW_DATA", "constant:CREDIT")

        # All loaded under the old constants: DRC DEBIT, Timeout CREDIT.
        self.drc_remitter = self._fact(drc_file, StagingDrc, 1, "SSK", "HDF", Direction.DEBIT)
        self.drc_beneficiary = self._fact(drc_file, StagingDrc, 2, "ICI", "SSK", Direction.DEBIT)
        # Archived date: party cells only in staging history.
        self.drc_archived = self._fact(drc_file, StagingDrc, 3, "ICI", "SSK", Direction.DEBIT, archive=True)
        self.drc_neither = self._fact(drc_file, StagingDrc, 4, "ICI", "HDF", Direction.DEBIT)
        self.timeout_remitter = self._fact(timeout_file, StagingTimeout, 5, "SSK", "HDF", Direction.CREDIT)
        self.timeout_beneficiary = self._fact(timeout_file, StagingTimeout, 6, "ICI", "SSK", Direction.CREDIT)
        # Adjustment, loaded under the old constant CREDIT; mirrored mapping.
        self.adjustment_remitter = self._fact(
            adjustment_file, StagingAdjustment, 7, "SSK", "HDF", Direction.CREDIT, cells_cols=("5", "6")
        )
        self.adjustment_beneficiary = self._fact(
            adjustment_file, StagingAdjustment, 8, "ICI", "SSK", Direction.CREDIT, cells_cols=("5", "6")
        )
        # Not an ssk_party config: must be left alone even though col 9 is SSK.
        self.constant = self._fact(constant_file, StagingRawData, 9, "SSK", "", Direction.CREDIT)

    def _file(self, code, rule):
        config = FileTypeConfig.objects.create(
            payment_module=self.payment_module, file_type_code=code, register_type=code,
            payload_format=PayloadFormat.CSV, direction_derivation_rule=rule,
        )
        return _build_uploaded_file(self.payment_module, config, filename=f"{code}.csv", sha256_hash=f"h-{code}")

    def _fact(self, uploaded_file, staging_model, n, remitter, beneficiary, direction, archive=False,
              cells_cols=("9", "10")):
        raw_cells = {cells_cols[0]: remitter, cells_cols[1]: beneficiary}
        staging = staging_model.objects.create(
            uploaded_file=uploaded_file, row_number=n, row_reference=f":{n}", raw_cells=raw_cells
        )
        staging_row_id = staging.id
        if archive:
            StagingDrcHistory.objects.create(
                original_id=staging.id, uploaded_file=uploaded_file, row_number=n,
                row_reference=staging.row_reference, raw_cells=raw_cells, created_at=staging.created_at,
            )
            staging.delete()
        return _build_fact(
            self.payment_module, uploaded_file,
            source_register_type=uploaded_file.file_type_config.register_type,
            rrn=f"94911431739{n}", row_reference=f":{n}", direction=direction, staging_row_id=staging_row_id,
        )

    def _run(self, *args):
        out = StringIO()
        call_command("recompute_ssk_party_directions", *args, stdout=out)
        return out.getvalue()

    def _directions(self):
        return dict(TransactionFact.objects.values_list("id", "direction"))

    def test_dry_run_changes_nothing(self):
        before = self._directions()
        output = self._run("--dry-run")
        # drc_beneficiary, drc_archived, drc_neither, timeout_remitter, adjustment_beneficiary.
        self.assertIn("Would update 5 of 8 rows", output)
        self.assertEqual(self._directions(), before)

    def test_recomputes_from_staging_and_history(self):
        output = self._run()
        self.assertIn("Updated 5 of 8 rows", output)
        self.assertIn("UNKNOWN (neither party is the bank): 1", output)

        for fact, expected in (
            (self.drc_remitter, Direction.DEBIT),
            (self.drc_beneficiary, Direction.CREDIT),
            (self.drc_archived, Direction.CREDIT),
            (self.drc_neither, Direction.UNKNOWN),
            (self.timeout_remitter, Direction.DEBIT),
            (self.timeout_beneficiary, Direction.CREDIT),
            (self.adjustment_remitter, Direction.CREDIT),  # mirrored: SSK payer refunded
            (self.adjustment_beneficiary, Direction.DEBIT),  # mirrored: taken back from SSK payee
            (self.constant, Direction.CREDIT),
        ):
            with self.subTest(fact=fact.row_reference):
                fact.refresh_from_db()
                self.assertEqual(fact.direction, expected)

    def test_second_run_is_a_no_op(self):
        self._run()
        before = self._directions()
        output = self._run()
        self.assertIn("Updated 0 of 8 rows", output)
        self.assertEqual(self._directions(), before)

    def test_fact_without_source_row_is_reported_and_left_alone(self):
        StagingDrc.objects.filter(id=self.drc_beneficiary.staging_row_id).delete()
        output = self._run()
        self.assertIn("No source row found: 1", output)
        self.drc_beneficiary.refresh_from_db()
        self.assertEqual(self.drc_beneficiary.direction, Direction.DEBIT)
        self.assertEqual(self.drc_beneficiary.source_register_type, RegisterType.DRC)
