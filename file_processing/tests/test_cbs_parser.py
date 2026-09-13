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

SAMPLE_ROOT = Path(__file__).resolve().parents[2] / "14072026"
CBS_SAMPLE = SAMPLE_ROOT / "cbs_report" / "b5rg090oa.xls"


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

    def test_rrn_extraction_flags_non_12_digit_rrn_without_crashing(self):
        rows = list(parse_spreadsheetml(CBS_SAMPLE))
        remarks_by_row = [
            (row.worksheet_name, row.row_number, row.cells.get(3))
            for row in rows
            if row.cells.get(3, "") and row.cells[3].startswith(("By UPI", "To UPI"))
        ]
        self.assertGreater(len(remarks_by_row), 0)

        valid_count = 0
        invalid_count = 0
        found_invalid_example = False
        for _ws, _rn, remarks in remarks_by_row:
            direction, rrn, rrn_valid = _extract_cbs_rrn(remarks, expected_digits=12)
            self.assertIn(direction, ("CREDIT", "DEBIT"))
            self.assertIsNotNone(rrn)
            if rrn_valid:
                valid_count += 1
            else:
                invalid_count += 1
                found_invalid_example = True

        self.assertTrue(found_invalid_example, "expected at least one non-12-digit RRN row in this sample")
        # Confirmed directly against this sample: ~9.86% invalid in the
        # first worksheet alone; assert the overall rate stays in a
        # sane band (not exactly pinned, to avoid over-fitting the test to
        # one measurement) rather than crashing on those rows.
        invalid_rate = invalid_count / (valid_count + invalid_count)
        self.assertGreater(invalid_rate, 0.05)
        self.assertLess(invalid_rate, 0.20)

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
        self.assertEqual(rrn2, "3354926952")
        self.assertFalse(valid2)  # only 10 digits, not the expected 12

    def test_remarks_regex_ignores_non_upi_remarks(self):
        self.assertIsNone(_CBS_REMARKS_RE.match("Interest Credit"))
