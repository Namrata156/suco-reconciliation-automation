"""
Confirms the NTSL OOXML-zip parser correctly reads a real sample file
despite its misleading `.xls` extension, and correctly resolves shared
strings + honors sparse cell references (not naive column-order parsing).
"""
from __future__ import annotations

from pathlib import Path

from django.test import SimpleTestCase

from file_processing.parsers.ntsl_ooxml import parse_ooxml_workbook

# Moved from the old top-level 14072026/NTSL/ folder (2026-10-02); same file, same SHA-256.
SAMPLE_ROOT = Path(__file__).resolve().parents[2] / "sample_decrypted_files" / "14072026"
NTSL_SAMPLE = SAMPLE_ROOT / "UPI_NTSLSSK140726_1C.xls"


class NtslOoxmlParserTests(SimpleTestCase):
    def test_parses_rows_and_resolves_shared_strings(self):
        rows = list(parse_ooxml_workbook(NTSL_SAMPLE))
        self.assertGreater(len(rows), 0)

        # Row 3 (per direct inspection) holds the settlement statement's
        # title text, resolved via sharedStrings rather than left as a
        # raw shared-string index.
        title_row = next(r for r in rows if r.row_number == 3)
        title_text = title_row.cells.get(1, "")
        self.assertIn("Daily Settlement Statement", title_text)

    def test_sparse_cell_references_do_not_shift_column_meaning(self):
        rows = list(parse_ooxml_workbook(NTSL_SAMPLE))
        # Row 1 has only columns A and E populated (confirmed directly
        # against the sample) -- column B/C/D must read as absent, not
        # shifted in from column E's value.
        row1 = next(r for r in rows if r.row_number == 1)
        self.assertIn(1, row1.cells)
        self.assertNotIn(2, row1.cells)
        self.assertNotIn(3, row1.cells)
