"""
Unit tests for `file_processing.tasks._row_date_iso`, the shared helper
used by `validate_structure_and_content` (RANGE_CONTAINS distinct-date
detection), `normalize_rows` (RANGE_CONTAINS row filtering), and Stage 4's
`load_to_unified_table` (per-row business_date resolution for EXACT_MATCH
types whose row date can genuinely differ, e.g. Adjustment).

Covers every date format confirmed present across the real sample files
this pipeline handles: CBS GL Extract's ISO datetime-with-"T"
(14072026/cbs_report/b5rg090oa.xls, the only format found across all
154,661 rows), DRC/Timeout's "DD/MM/YYYY" ("TXN Date" column), and
Adjustment's "DD-MM-YYYY" ("Adjdate" column) -- plus the defensive
bare-ISO-date fallback and the requirement that non-date input (e.g. a
stray "Date" column-header label) must resolve to None rather than being
silently sliced into a fake date string.
"""
from __future__ import annotations

from django.test import SimpleTestCase

from file_processing.tasks import _row_date_iso


class RowDateIsoTests(SimpleTestCase):
    def test_iso_datetime_with_t_is_normalized(self):
        # The only date format actually present in the real CBS sample.
        cells = {"1": "2026-07-13T00:00:00.000"}
        self.assertEqual(_row_date_iso(cells, "1"), "2026-07-13")

    def test_bare_iso_date_without_t_is_normalized(self):
        # Defensive fallback -- not exercised by the current sample, but
        # handled explicitly rather than relying on blind slicing.
        cells = {"1": "2026-07-14"}
        self.assertEqual(_row_date_iso(cells, "1"), "2026-07-14")

    def test_non_date_label_returns_none_not_sliced_garbage(self):
        # The exact real-world case that slipped through before this fix:
        # worksheet "Report"'s own column-header row, whose date cell
        # literally contains the string "Date".
        cells = {"1": "Date"}
        self.assertIsNone(_row_date_iso(cells, "1"))

    def test_malformed_date_returns_none(self):
        cells = {"1": "2026-13-99"}
        self.assertIsNone(_row_date_iso(cells, "1"))

    def test_missing_cell_returns_none(self):
        cells = {"1": ""}
        self.assertIsNone(_row_date_iso(cells, "1"))
        self.assertIsNone(_row_date_iso({}, "1"))

    def test_no_date_column_configured_returns_none(self):
        cells = {"1": "2026-07-13T00:00:00.000"}
        self.assertIsNone(_row_date_iso(cells, ""))

    def test_dd_slash_mm_slash_yyyy_is_normalized(self):
        # DRC / Timeout's "TXN Date" column, confirmed format via real sample
        # (Debit Reversal Confirmation Report__SSK_2026-07-14.csv, "14/07/2026").
        cells = {"1": "14/07/2026"}
        self.assertEqual(_row_date_iso(cells, "1"), "2026-07-14")

    def test_dd_dash_mm_dash_yyyy_is_normalized(self):
        # Adjustment's "Adjdate" column, confirmed format via real sample
        # (UPI Adjustment Report_SSK_2026-07-14.csv, "14-07-2026" and, on a
        # different row, "15-07-2026" -- genuinely varies per row, see
        # doubts.md's Adjustment resolution).
        cells = {"1": "14-07-2026"}
        self.assertEqual(_row_date_iso(cells, "1"), "2026-07-14")
        cells = {"1": "15-07-2026"}
        self.assertEqual(_row_date_iso(cells, "1"), "2026-07-15")

    def test_dd_dash_mm_dash_yyyy_does_not_collide_with_bare_iso_date(self):
        # _DATE_ONLY_RE (YYYY-MM-DD, 4-2-2 digit grouping) must be checked
        # before the DD-MM-YYYY (2-2-4 grouping) pattern, or a bare ISO date
        # could be misread. Confirms both still resolve correctly together.
        cells = {"1": "2026-07-14"}
        self.assertEqual(_row_date_iso(cells, "1"), "2026-07-14")

    def test_malformed_dd_slash_mm_slash_yyyy_returns_none(self):
        cells = {"1": "32/13/2026"}
        self.assertIsNone(_row_date_iso(cells, "1"))

    def test_malformed_dd_dash_mm_dash_yyyy_returns_none(self):
        cells = {"1": "32-13-2026"}
        self.assertIsNone(_row_date_iso(cells, "1"))
