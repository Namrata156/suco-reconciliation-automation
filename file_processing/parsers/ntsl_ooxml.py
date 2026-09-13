"""
Parser for NTSL's real format: an OOXML zip package (despite the `.xls`
extension -- see parsers/sniff.py and the approved plan's "Key findings").
Structurally this is a plain single-sheet .xlsx: `[Content_Types].xml` +
`xl/worksheets/sheet1.xml` (+ `xl/sharedStrings.xml` for text cells
referenced by index).

Like the CBS SpreadsheetML parser, cell references (`r="A1"`, `r="B4"`,
...) must be parsed explicitly rather than assuming column order -- blank
cells are omitted from a `<row>` element the same way SpreadsheetML omits
them. Confirmed directly against `14072026/NTSL/UPI_NTSLSSK140726_1C.xls`:
header rows there have gaps (e.g. only columns A and E populated on some
rows).

This parser is deliberately generic (workbook/sheet/sharedStrings only) --
it does not know NTSL's specific settlement-column semantics (this sample
file is a per-cycle settlement summary keyed by transaction-category
description, not an RRN-level register). Column meaning is applied later
via `FileTypeConfig.column_mapping` in the normalize_rows stage, matching
the same separation used for CBS.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_CELL_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")


@dataclass
class ParsedRow:
    sheet_name: str
    row_number: int  # 1-based position within its worksheet
    cells: dict[int, str | None] = field(default_factory=dict)  # 1-based column index -> value


def _col_letters_to_index(letters: str) -> int:
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx


def _load_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for si in root.findall(f"{_MAIN_NS}si"):
        # <si> can hold a plain <t> or multiple <r><t> runs (rich text) --
        # concatenate all text runs to get the full string either way.
        strings.append("".join(t.text or "" for t in si.iter(f"{_MAIN_NS}t")))
    return strings


def _sheet_member_names(zf: zipfile.ZipFile) -> list[str]:
    return sorted(
        n for n in zf.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
    )


def parse_ooxml_workbook(path: Path) -> Iterator[ParsedRow]:
    """
    Stream rows out of every worksheet in an OOXML zip package, in
    sheet/row document order, resolving shared-string cell values and
    honoring each cell's explicit `r="<col><row>"` reference.
    """
    with zipfile.ZipFile(path) as zf:
        shared_strings = _load_shared_strings(zf)
        for sheet_name in _sheet_member_names(zf):
            root = ET.fromstring(zf.read(sheet_name))
            sheet_data = root.find(f"{_MAIN_NS}sheetData")
            if sheet_data is None:
                continue
            for row_number, row_el in enumerate(sheet_data.findall(f"{_MAIN_NS}row"), start=1):
                cells: dict[int, str | None] = {}
                for c_el in row_el.findall(f"{_MAIN_NS}c"):
                    ref = c_el.get("r", "")
                    m = _CELL_REF_RE.match(ref)
                    if not m:
                        continue
                    col_idx = _col_letters_to_index(m.group(1))
                    cell_type = c_el.get("t")
                    v_el = c_el.find(f"{_MAIN_NS}v")
                    raw_value = v_el.text if v_el is not None else None
                    if cell_type == "s" and raw_value is not None:
                        value = shared_strings[int(raw_value)]
                    else:
                        value = raw_value
                    cells[col_idx] = value
                yield ParsedRow(sheet_name=sheet_name, row_number=row_number, cells=cells)
