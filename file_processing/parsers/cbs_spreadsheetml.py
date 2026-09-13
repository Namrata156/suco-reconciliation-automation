"""
Parser for CBS GL Extract's real format: SpreadsheetML 2003 XML (despite
the `.xls` extension -- see parsers/sniff.py and the approved plan's "Key
findings").

Must use a real XML parser and honor each Cell's `ss:Index` attribute
per-row, not naive positional (column-N-is-always-field-X) parsing --
SpreadsheetML omits cells that are empty/unstyled, and the following
cell's `ss:Index` jumps to fill the gap, so a row's raw sequential cell
position shifts on sparse rows. This is not a theoretical risk: confirmed
directly against `14072026/cbs_report/b5rg090oa.xls` -- most data rows
have Remarks at ss:Index 3 and Withdrawals at ss:Index 7, but at least one
row in the file presents Remarks at ss:Index 2 and Withdrawals at
ss:Index 4 instead. Naive positional parsing would silently misread
amount/direction on rows like that.

Streams via `xml.etree.ElementTree.iterparse` rather than a single
`ET.parse` call -- CBS GL Extract files run large (this sample: ~100MB,
154,661 rows across 3 `<Worksheet>` elements, spanning the report's whole
date range, not just one business date -- date-range row filtering happens
later, in `file_processing/tasks.py::normalize_rows`, per
`FileTypeConfig.date_validation_mode`).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

_NS = "{urn:schemas-microsoft-com:office:spreadsheet}"


@dataclass
class ParsedRow:
    worksheet_name: str
    row_number: int  # 1-based position within its worksheet -- used as row_reference for validation exceptions
    cells: dict[int, str | None] = field(default_factory=dict)  # ss:Index (1-based) -> cell text


def parse_spreadsheetml(path: Path) -> Iterator[ParsedRow]:
    """
    Stream rows out of a SpreadsheetML XML file across all worksheets, in
    document order, honoring each cell's `ss:Index` for positioning rather
    than assuming sequential column order.
    """
    worksheet_name = ""
    row_number = 0

    for event, elem in ET.iterparse(str(path), events=("start", "end")):
        tag = _local_tag(elem.tag)

        if event == "start" and tag == "Worksheet":
            worksheet_name = elem.get(f"{_NS}Name", "")
            row_number = 0

        elif event == "end" and tag == "Row":
            row_number += 1
            cells: dict[int, str | None] = {}
            next_index = 1
            for cell_el in elem.findall(f"{_NS}Cell"):
                idx_attr = cell_el.get(f"{_NS}Index")
                idx = int(idx_attr) if idx_attr is not None else next_index
                data_el = cell_el.find(f"{_NS}Data")
                cells[idx] = data_el.text if data_el is not None else None
                next_index = idx + 1
            yield ParsedRow(worksheet_name=worksheet_name, row_number=row_number, cells=cells)
            elem.clear()

        elif event == "end" and tag == "Worksheet":
            elem.clear()


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag
