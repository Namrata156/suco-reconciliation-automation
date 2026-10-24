"""
Column-by-column comparison of a generated NPCI file with a reference file
(e.g. ops' own SSKUPI file). Used by `manage.py check_npci_file` (design
section 7, Slice 1 "a command that compares a generated file with a sample
file"). Reports counts; any difference is shown with masked values only, so
the output never carries a full RRN or account number.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

COLUMNS = ("bankadjref", "flag", "Shtdat", "adjamt", "shser", "shcrd", "filename", "reason", "specifyother")


def mask(value: str) -> str:
    value = value or ""
    return "*" * max(0, len(value) - 4) + value[-4:]


@dataclass
class Comparison:
    header_same: bool
    line_endings: dict  # {"generated": "CRLF"/"LF"/"mixed", "reference": ...}
    generated_rows: int
    reference_rows: int
    both: int
    only_generated: list[str]
    only_reference: list[str]
    fields_compared: int
    fields_matching: int
    order_same: bool
    mismatches: list[dict] = field(default_factory=list)
    column_mismatch_counts: dict = field(default_factory=dict)
    ignored_columns: tuple = ()
    exact_bytes: bool = False

    def lines(self) -> list[str]:
        out = [
            f"header same: {self.header_same}; line endings: generated {self.line_endings['generated']}, "
            f"reference {self.line_endings['reference']}; byte-identical: {self.exact_bytes}",
            f"rows: generated {self.generated_rows} / reference {self.reference_rows} / both {self.both}",
            f"fields matching (all columns except {', '.join(self.ignored_columns) or 'none'}): "
            f"{self.fields_matching} of {self.fields_compared}",
            f"row order same: {self.order_same}",
        ]
        if self.column_mismatch_counts:
            out.append("mismatches per column: " + ", ".join(f"{c} {n}" for c, n in self.column_mismatch_counts.items()))
        for m in self.mismatches:
            out.append(f"  {m['column']}: rrn {m['rrn']} {m['flag']}: generated {m['generated']!r} "
                       f"reference {m['reference']!r}")
        for key in self.only_generated:
            out.append(f"  only in generated: {key}")
        for key in self.only_reference:
            out.append(f"  only in reference: {key}")
        return out


def _ending(data: bytes) -> str:
    crlf, lf = data.count(b"\r\n"), data.count(b"\n")
    if lf == 0:
        return "none"
    return "CRLF" if crlf == lf else "LF" if crlf == 0 else "mixed"


def _rows(text: str) -> tuple[str, list[list[str]]]:
    lines = [line for line in text.replace("\r\n", "\n").split("\n") if line]
    return (lines[0] if lines else ""), [line.split(",") for line in lines[1:]]


def compare(generated: bytes, reference: bytes, *, ignore=("specifyother",), show_masked=(
        "bankadjref", "shser", "shcrd")) -> Comparison:
    g_header, g_rows = _rows(generated.decode("ascii", errors="replace"))
    r_header, r_rows = _rows(reference.decode("ascii", errors="replace"))

    def keyed(rows):
        seen, out = Counter(), {}
        for row in rows:
            base = (row[0], row[1]) if len(row) > 1 else (row[0] if row else "", "")
            seen[base] += 1
            out[(*base, seen[base])] = row
        return out

    g, r = keyed(g_rows), keyed(r_rows)
    common = [k for k in g if k in r]
    compared = matching = 0
    per_column: Counter = Counter()
    mismatches = []
    for k in common:
        for i, column in enumerate(COLUMNS):
            if column in ignore:
                continue
            a = g[k][i] if i < len(g[k]) else ""
            b = r[k][i] if i < len(r[k]) else ""
            compared += 1
            if a == b:
                matching += 1
                continue
            per_column[column] += 1
            shown = (mask(a), mask(b)) if column in show_masked else (a, b)
            mismatches.append({"column": column, "rrn": mask(k[0]), "flag": k[1], "generated": shown[0],
                               "reference": shown[1]})
    order_g = [k for k in g if k in r]
    order_r = [k for k in r if k in g]
    return Comparison(
        header_same=g_header == r_header,
        line_endings={"generated": _ending(generated), "reference": _ending(reference)},
        generated_rows=len(g_rows), reference_rows=len(r_rows), both=len(common),
        only_generated=[f"{mask(k[0])} {k[1]}" for k in g if k not in r],
        only_reference=[f"{mask(k[0])} {k[1]}" for k in r if k not in g],
        fields_compared=compared, fields_matching=matching, order_same=order_g == order_r,
        mismatches=mismatches, column_mismatch_counts=dict(per_column), ignored_columns=tuple(ignore),
        exact_bytes=generated == reference,
    )
