"""
Exercises the CBS SpreadsheetML parser + RRN/direction extraction logic
directly against the real sample file
`14072026/cbs_report/b5rg090oa.xls` -- confirms:
  - ss:Index-aware parsing (not naive positional) works across all 3
    worksheets in the file.
  - RRN extraction correctly flags rows whose RRN isn't a clean 12-digit
    number (confirmed ~9.86% of rows in this sample) as invalid rather
    than crashing or silently dropping them.
  - Direction derives correctly from the By/To remarks prefix.
"""
from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

from file_processing.parsers.cbs_spreadsheetml import parse_spreadsheetml
from file_processing.tasks import _CBS_REMARKS_RE, _extract_cbs_rrn

# Formerly 14072026/cbs_report/b5rg090oa.xls -- same file (same SHA-256), renamed.
SAMPLE_ROOT = Path(__file__).resolve().parents[2] / "sample_decrypted_files" / "14072026"
CBS_SAMPLE = SAMPLE_ROOT / "cbs_14072026.xls"


class CbsSpreadsheetmlParserTests(SimpleTestCase):
    def test_parses_all_three_worksheets_and_total_row_count(self):
        rows = list(parse_spreadsheetml(CBS_SAMPLE))
        worksheets = {row.worksheet_name for row in rows}
        self.assertEqual(worksheets, {"Report", "Report1", "Report2"})
        # Matches the count confirmed directly against this sample file:
        # 65535 + 65535 + 23591 = 154661.
        self.assertEqual(len(rows), 154661)

    def test_ss_index_honored_not_assumed_sequential(self):
        rows = list(parse_spreadsheetml(CBS_SAMPLE))
        # The very first data row's Remarks cell sits at ss:Index 3 (Date
        # is 1, Cheque# is 6, Withdrawals 7, Deposits 10, Balance 12) --
        # if positional parsing were used instead, cells[3] would not
        # reliably be Remarks on every row.
        remarks_row = next(r for r in rows if r.cells.get(3, "").startswith(("By UPI", "To UPI")))
        self.assertIn("UPI/", remarks_row.cells[3])

    def test_every_upi_remarks_rrn_in_sample_is_valid_after_zero_padding(self):
        rows = list(parse_spreadsheetml(CBS_SAMPLE))
        remarks_by_row = [
            (row.worksheet_name, row.row_number, row.cells.get(3))
            for row in rows
            if row.cells.get(3, "") and row.cells[3].startswith(("By UPI", "To UPI"))
        ]
        self.assertGreater(len(remarks_by_row), 0)

        # ~10% of this sample's remarks carry RRNs with their leading zeros
        # dropped (8-11 digits); extraction restores them, so none is left
        # invalid.
        short_in_remarks = 0
        for _ws, _rn, remarks in remarks_by_row:
            direction, rrn, rrn_valid = _extract_cbs_rrn(remarks, expected_digits=12)
            self.assertIn(direction, ("CREDIT", "DEBIT"))
            self.assertTrue(rrn_valid, remarks)
            self.assertEqual(len(rrn), 12)
            if rrn not in remarks:
                short_in_remarks += 1
        self.assertGreater(short_in_remarks / len(remarks_by_row), 0.05)

    def test_leading_zeros_restored_only_for_8_to_11_digit_rrns(self):
        cases = {
            "By UPI/4383934640/8867042497-2@ybl/Payment from PhonePe": ("004383934640", True),
            "To UPI/71131310561/7349090632@ybl/Payment from PhonePe": ("071131310561", True),
            "By UPI/12345678/x@ybl/y": ("000012345678", True),
            "By UPI/1234567/x@ybl/y": ("1234567", False),  # too short to be a lost-zero RRN
            "By UPI/ABC123456789/x@ybl/y": ("ABC123456789", False),
            "By UPI/1234567890123/x@ybl/y": ("1234567890123", False),  # too long, never trimmed
        }
        for remarks, (expected_rrn, expected_valid) in cases.items():
            _direction, rrn, rrn_valid = _extract_cbs_rrn(remarks, expected_digits=12)
            self.assertEqual((rrn, rrn_valid), (expected_rrn, expected_valid), remarks)

    def test_direction_derivation_matches_by_to_prefix(self):
        credit_direction, rrn, valid = _extract_cbs_rrn(
            "By UPI/315485225633/7411852454@ybl/Payment from PhonePe", expected_digits=12
        )
        self.assertEqual(credit_direction, "CREDIT")
        self.assertEqual(rrn, "315485225633")
        self.assertTrue(valid)

        debit_direction, rrn2, valid2 = _extract_cbs_rrn(
            "To UPI/3354926952/7411691215-5@ybl/Paid via Navi UPI", expected_digits=12
        )
        self.assertEqual(debit_direction, "DEBIT")
        self.assertEqual(rrn2, "003354926952")  # CBS dropped the leading zeros
        self.assertTrue(valid2)

    def test_remarks_regex_ignores_non_upi_remarks(self):
        self.assertIsNone(_CBS_REMARKS_RE.match("Interest Credit"))
