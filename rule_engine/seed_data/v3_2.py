"""
UPI business rule table v3.2 as plain data -- docs/phase2_rule_table_v3_approved.txt,
"v3.2 AMENDMENT" (approved by the project owner 2026-10-10): "we only work
on that particular day". Each business date is reconciled only against its
own files; there is no cross-day check in either direction.

v3.2 = v3.1 with ONE change: R13 (In CBS only) becomes Pending
Investigation, MEDIUM, due T+1 working day on its own business date. A
maker investigates (e.g. checks the next day's NPCI file by hand) and
decides; a checker approves. No automatic refund (same "no automatic money
action" approach as R06b). The v3 / v3.1 next-business-date-load TAT is
gone from this version (TatType.NEXT_BUSINESS_DATE_LOAD stays in the enum:
retired v3.1 data still uses it).

FROZEN, like v3_1.py. Built from v3_1's frozen data so "every other rule is
identical" holds by construction (rule_engine/tests/test_seed_v3_2.py also
checks it field by field). Plain data only: imports nothing but v3_1.

Rule codes match the same groups as in v3.1, so EXPECTED_COUNTS are
unchanged (doc: "Rule counts: unchanged (R13 matches the same groups:
1 / 10 / 578)").
"""
from __future__ import annotations

import copy
from datetime import date

from rule_engine.seed_data import v3_1

VERSION_LABEL = "v3.2"
SUPERSEDES = "v3.1"  # seeding may retire exactly this active version, nothing else
PAYMENT_MODULE_CODE = v3_1.PAYMENT_MODULE_CODE
EFFECTIVE_FROM = date(2026, 10, 10)  # approval date of the v3.2 amendment
SOURCE_DOCUMENT = (
    "docs/phase2_rule_table_v3_approved.txt -- v3 approved 2026-10-09 + v3.1 amendment approved "
    "2026-10-09 + v3.2 amendment approved 2026-10-10 (project owner)"
)
RULE_SET_NOTES = (
    "v3.2: each business date is reconciled only against its own files, no cross-day checks (owner "
    "2026-10-10); R13 is a T+1 review item. Known effect: a payment NPCI records on D and CBS posts on "
    "D+1 shows as R12 on D and R13 on D+1; ops resolves these by hand. " + v3_1.RULE_SET_NOTES
)

RESPONSE_CODES = v3_1.RESPONSE_CODES
RET_ALTERNATIVES = v3_1.RET_ALTERNATIVES
AGEING_SOURCE = v3_1.AGEING_SOURCE
AGEING_BUCKETS = v3_1.AGEING_BUCKETS

R13 = dict(
    code="R13",
    name="In CBS only",
    condition=copy.deepcopy(next(r for r in v3_1.RULES if r["code"] == "R13")["condition"]),
    status="PENDING_INVESTIGATION",
    action=(
        "Maker investigates (for example, checks the next day's NPCI file by hand) and decides; the "
        "checker approves. No automatic refund."
    ),
    priority="MEDIUM",
    tat_type="DAYS",
    tat_days=1,
    tat_day_basis="WORKING",
    tat_note="T+1 (working days), on its own business date.",
    reason_template=(
        "In CBS only, not in this date's NPCI files. Maker to investigate (e.g. check the next day's NPCI "
        "file by hand) and decide; checker approves. No automatic refund."
    ),
    source="Owner (v3.2 amendment, 2026-10-10; reverses Q3's next-day re-check)",
    notes=(
        "v3.2: no cross-day check in either direction ('we only work on that particular day'). Not "
        "'Reversal Required': on 26 Sep most of the 578 CBS-only payments are 27 Sep transactions (RRN "
        "day code 270) that NPCI records the next day, so an automatic refund would pay those customers "
        "twice."
    ),
)

RULES = [copy.deepcopy(R13) if r["code"] == "R13" else copy.deepcopy(r) for r in v3_1.RULES]

EXPECTED_COUNTS = v3_1.EXPECTED_COUNTS
EXPECTED_TOTALS = v3_1.EXPECTED_TOTALS
EXPECTED_COUNT_DATES = v3_1.EXPECTED_COUNT_DATES
