"""
Generic CSV parser for source file types whose real payload format is
plain delimited text (e.g. DRC, Timeout, Adjustment reports -- delivered
as `.csv.pgp`, decrypted down to plain CSV). Delimiter/encoding come from
`FileTypeConfig` rather than being hardcoded, since the spec doesn't
guarantee every CSV source uses the same delimiter/encoding.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass
class ParsedRow:
    row_number: int  # 1-based, including the header row if has_header=True
    cells: dict[int, str | None] = field(default_factory=dict)  # 1-based column index -> value
    header: dict[int, str] | None = None  # populated on data rows when has_header=True


def parse_csv(
    path: Path,
    delimiter: str = ",",
    encoding: str = "utf-8",
    has_header: bool = True,
) -> Iterator[ParsedRow]:
    """`has_header=False` for headerless payloads (e.g. Raw Data / Merchant
    Raw Data / Merchant Raw Data BBP's "HT" metadata first row -- see
    `FileTypeConfig.has_header`'s docstring): row 1 is then yielded as an
    ordinary data row rather than being consumed as a header, matching
    those file types' `pagination_artifact_patterns`-based HT/FT stripping
    instead of this parser's own header-skipping."""
    with open(path, newline="", encoding=encoding) as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        header: dict[int, str] | None = None
        for row_number, raw_row in enumerate(reader, start=1):
            cells = {i + 1: (v if v != "" else None) for i, v in enumerate(raw_row)}
            if has_header and row_number == 1:
                header = {i: v for i, v in cells.items() if v is not None}
                continue
            yield ParsedRow(row_number=row_number, cells=cells, header=header)
