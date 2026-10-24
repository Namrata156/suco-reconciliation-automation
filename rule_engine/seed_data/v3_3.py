"""
UPI business rule table v3.3 as plain data -- docs/phase2_rule_table_v3_approved.txt,
"v3.3 AMENDMENT" (decided by the project owner 2026-10-23): deadlines count
CALENDAR days. On calendar day D ops reconcile business date D-1, every day
of the year with no holidays (T+1 settlement per RBI/NPCI). This reverses
the owner's 2026-10-10 working-day rule (core/working_days.py, kept but not
used by v3.3).

v3.3 = v3.2 with ONE kind of change: every deadline whose basis was
WORKING (rules and R07b's TAT rows) becomes CALENDAR, with the same number
of days and the same cutoff time (R05 RET still by 16:00). R13's tat_note
said "(working days)" and now says "(calendar days)"; no other field
changes. So a T+1 deadline for business date D is D+1 whatever the weekday
(R05: D+1 16:00).

FROZEN, like v3_1.py / v3_2.py. Built from v3_2's frozen data so "nothing
else changes" holds by construction (rule_engine/tests/test_seed_v3_3.py
checks it field by field). Plain data only: imports nothing but v3_2.

Conditions are unchanged, so EXPECTED_COUNTS are unchanged.
"""
from __future__ import annotations

import copy
from datetime import date

from rule_engine.seed_data import v3_2

VERSION_LABEL = "v3.3"
SUPERSEDES = "v3.2"  # seeding may retire exactly this active version, nothing else
PAYMENT_MODULE_CODE = v3_2.PAYMENT_MODULE_CODE
EFFECTIVE_FROM = date(2026, 10, 23)  # owner decision date
SOURCE_DOCUMENT = v3_2.SOURCE_DOCUMENT + " + v3.3 amendment decided 2026-10-23 (project owner)"
RULE_SET_NOTES = (
    "v3.3: deadlines count calendar days, not working days (owner 2026-10-23: reconciliation runs on D for "
    "business date D-1, every calendar day, no holidays); same days and cutoff times as v3.2. "
    + v3_2.RULE_SET_NOTES
)

RESPONSE_CODES = v3_2.RESPONSE_CODES
RET_ALTERNATIVES = v3_2.RET_ALTERNATIVES
AGEING_SOURCE = v3_2.AGEING_SOURCE
AGEING_BUCKETS = v3_2.AGEING_BUCKETS

OLD_BASIS, NEW_BASIS = "WORKING", "CALENDAR"
# The one wording that named the basis (v3.2's R13).
TAT_NOTE_REWORDING = {"T+1 (working days), on its own business date.": "T+1 (calendar days), on its own business date."}


def _calendar(rule: dict) -> dict:
    r = copy.deepcopy(rule)
    if r.get("tat_day_basis") == OLD_BASIS:
        r["tat_day_basis"] = NEW_BASIS
    if r.get("tat_note") in TAT_NOTE_REWORDING:
        r["tat_note"] = TAT_NOTE_REWORDING[r["tat_note"]]
    if "tat_rows" in r:
        r["tat_rows"] = [
            (order, label, cond, days, NEW_BASIS if basis == OLD_BASIS else basis, source, notes)
            for order, label, cond, days, basis, source, notes in r["tat_rows"]
        ]
    return r


RULES = [_calendar(r) for r in v3_2.RULES]

EXPECTED_COUNTS = v3_2.EXPECTED_COUNTS
EXPECTED_TOTALS = v3_2.EXPECTED_TOTALS
EXPECTED_COUNT_DATES = v3_2.EXPECTED_COUNT_DATES
