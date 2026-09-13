"""
Confirms pagination/header/footer stripping is content-pattern based
(matches header/GL-info/opening-balance/footer rows regardless of their
position or repetition count), not row-count based -- see
parsers/pagination_strip.py docstring for why that distinction matters.

Audit note (2026-09-05): every one of the real sample's 154,661 rows
across all 3 worksheets (Report/Report1/Report2 -- an artifact of Excel's
65,536-row-per-sheet limit splitting one continuous report, confirmed by
the fact that data rows flow across the worksheet boundary with no
repeated header/footer/"continued" marker at the split points) was
classified as either a real transaction data row or one of the non-data
row types the patterns above now cover. No other non-data row types were
found, and nothing ambiguous turned up requiring a human judgment call.
"""
from __future__ import annotations

from django.test import SimpleTestCase

from file_processing.parsers.pagination_strip import strip_pagination_artifacts

# Patterns matching the real CBS GL Extract sample's header/GL-info/
# opening-balance/footer content (confirmed directly against
# 14072026/cbs_report/b5rg090oa.xls, all 154,661 rows across its 3
# worksheets audited row-by-row -- see audit notes in this module's
# docstring below for what else was checked and ruled out).
#
# This is the canonical reference list -- until a real FileTypeConfig row
# exists for CBS_GL_EXTRACT (see claude.md's stub list), this is what a
# human would copy into `FileTypeConfig.pagination_artifact_patterns` when
# creating that config via Django admin. Other modules/tests should import
# this list rather than keep their own divergent copy.
CBS_ARTIFACT_PATTERNS = [
    r"^Suco Souharda Sahakari Bank Ltd",
    r"^Head Office",
    r"^Statement Of Office Account",
    r"^From\s+\d",
    r"^GL No\.",
    r"^Account NBR",
    r"^Opening Balance",
    # Worksheet "Report"'s own column-header row (Date/Remarks/Cheque#/
    # Withdrawals/Deposits/Balance, row 8) -- not caught by any pattern
    # above and, being un-parseable as a real calendar date, would
    # otherwise leak a spurious "Date" entry into detected_dates.
    r"^Date\s+Remarks",
    # Report-end summation/totals row (worksheet "Report2", row 23590:
    # "Summation of Debits and Credit..." followed by aggregate totals,
    # not a real transaction -- no date, and the literal cell text really
    # does end in a truncated "..." in the source report).
    r"^Summation of Debits and Credit",
    r"^Printed By",
    r"^Pages\s*:",
]


class PaginationStripTests(SimpleTestCase):
    def test_strips_header_and_footer_rows_regardless_of_position(self):
        rows = [
            ["Suco Souharda Sahakari Bank Ltd"],
            ["Head Office"],
            ["Statement Of Office Account"],
            ["From  13/07/2026 To 15/07/2026"],
            ["GL No. : ", "1535"],
            ["Opening Balance B/F : ", "8241232.77", "Cr"],
            ["2026-07-14T00:00:00.000", "By UPI/315485225633/x@ybl/desc", None, None, "100"],
            ["2026-07-14T00:00:00.000", "To UPI/999999999999/y@ybl/desc", None, "50", None],
            ["Printed By :", "Mr. X@15/07/2026 09:51:48", "Pages :", "1", "of", "155"],
        ]
        kept, stripped_count = strip_pagination_artifacts(rows, CBS_ARTIFACT_PATTERNS)

        self.assertEqual(stripped_count, 7)
        self.assertEqual(len(kept), 2)
        self.assertTrue(any("315485225633" in str(c) for row in kept for c in row))
        self.assertTrue(any("999999999999" in str(c) for row in kept for c in row))

    def test_does_not_over_strip_when_no_artifacts_present(self):
        rows = [["a", "b"], ["c", "d"]]
        kept, stripped_count = strip_pagination_artifacts(rows, CBS_ARTIFACT_PATTERNS)
        self.assertEqual(stripped_count, 0)
        self.assertEqual(kept, rows)

    def test_strips_column_header_row(self):
        """Worksheet "Report"'s own column-header row (row 8 in the real
        sample) -- literal cell text confirmed directly against
        14072026/cbs_report/b5rg090oa.xls."""
        rows = [
            ["Date", "Remarks", "Cheque#", "Withdrawals", "Deposits", "Balance"],
            ["2026-07-13T00:00:00.000", "By UPI/315485225633/x@ybl/desc", None, None, "1100"],
        ]
        kept, stripped_count = strip_pagination_artifacts(rows, CBS_ARTIFACT_PATTERNS)
        self.assertEqual(stripped_count, 1)
        self.assertEqual(len(kept), 1)
        self.assertTrue(any("315485225633" in str(c) for c in kept[0]))

    def test_strips_summation_footer_row(self):
        """Report-end summation/totals row (row 23590 in worksheet "Report2"
        of the real sample) -- literal cell text (including its trailing
        "...") confirmed directly against the real file."""
        rows = [
            ["2026-07-15T00:00:00.000", "By UPI/637531491130/9901668838-2@ybl/desc", "9000", "1310675.19"],
            ["Summation of Debits and Credit...", "1.3913202859E8", "1.3220147101E8", "1325797.4", "Cr"],
        ]
        kept, stripped_count = strip_pagination_artifacts(rows, CBS_ARTIFACT_PATTERNS)
        self.assertEqual(stripped_count, 1)
        self.assertEqual(len(kept), 1)
        self.assertTrue(any("637531491130" in str(c) for c in kept[0]))
