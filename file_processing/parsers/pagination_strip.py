"""
Content-pattern based stripping of CBS header/footer/pagination-artifact
rows.

Deliberately NOT row-count-based (e.g. "always drop the first N rows").
Confirmed directly against the real sample CBS export
(`14072026/cbs_report/b5rg090oa.xls`): header/GL-info/opening-balance rows
and the report footer ("Printed By :" / "Pages : 1 of 155") each appear
exactly once in the whole multi-worksheet report -- at the true start and
end -- not once per printed page. A row-count approach would either strip
real data (if it over-assumes repetition that isn't there) or leave
artifacts in (if a future export genuinely does repeat per page). Matching
each row's content against configured patterns is robust to both.
"""
from __future__ import annotations

import re
from typing import Sequence


def strip_pagination_artifacts(
    rows: Sequence[Sequence[str | None]],
    patterns: Sequence[str],
) -> tuple[list[list[str | None]], int]:
    """
    rows: parsed rows, each a sequence of cell values already positioned
        honoring ss:Index / cell-ref (see cbs_spreadsheetml.py /
        ntsl_ooxml.py) -- order within the sequence doesn't matter here,
        only the joined text content does.
    patterns: `FileTypeConfig.pagination_artifact_patterns` -- regexes
        tested (via `re.search`) against each row's concatenated non-empty
        cell text, e.g. [r'^Suco Souharda Sahakari Bank Ltd', r'^Head
        Office', r'^Statement Of Office Account', r'^From\\s+\\d',
        r'^GL No\\.', r'^Account NBR', r'^Opening Balance', r'^Printed By',
        r'^Pages\\s*:'] for the CBS GL Extract sample.

    Returns (data_rows, stripped_count).
    """
    compiled = [re.compile(p) for p in patterns]
    kept: list[list[str | None]] = []
    stripped = 0
    for row in rows:
        joined = " ".join(str(c) for c in row if c is not None)
        if any(p.search(joined) for p in compiled):
            stripped += 1
            continue
        kept.append(list(row))
    return kept, stripped
