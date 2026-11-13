"""
UPI business rule table v3.4 as plain data (owner decision 2026-11-11, the
CBS credit file ("rev" file) plan: follow ops' practice).

What ops do (2 Nov 2026 samples: the Adjustment report, ops' rev sheet
`upi rev 02112026.xls` and SSKUPI02112026.csv): a Credit Adjustment / Online
Refund / RET for an SSK-remitter payment (R06) is answered with RRC 501 at
once, and the customer is credited only when NPCI's "Refund Reversal
Confirmation" for that RRN arrives. The credit is posted in CBS through one
voucher file per day (the rev file, npci_files.credit_voucher).

v3.4 = v3.3 with these changes and nothing else:
  - NEW rule R07r "Adjustment - Refund Reversal Confirmation (SSK =
    Remitter)", placed just before R07a and taken out of it: Manual Credit
    Required, HIGH, T+1 calendar day. The credit goes through the rev file;
    there is NO SSKUPI reply (NPCI has already confirmed), so R07r has no
    response code.
  - R07a keeps the other confirmations (Debit Reversal Confirmation, TCC):
    its condition is unchanged, the Refund Reversal Confirmations simply
    reach R07r first (first match wins).
  - R06 now means "reply RRC 501; the credit follows when the confirmation
    arrives (R07r)". Its status category becomes
    RRC_REPLY_CREDIT_ON_CONFIRMATION, "RRC 501 reply – credit on
    confirmation" (owner 2026-11-12; it is no longer a pending manual
    credit). Condition, priority, deadline and response code are unchanged;
    the action text and notes change too. Its
    RRC 501 reply closes the case when it goes into an SSKUPI file
    (npci_files.auto_close.AUTO_CLOSE_REPLIES).

FROZEN, like v3_1.py .. v3_3.py. Built from v3_3's frozen data so "nothing
else changes" holds by construction (rule_engine/tests/test_seed_v3_4.py
checks it field by field). Plain data only: imports nothing but v3_3 / v3_1.

EXPECTED_COUNTS: the v3.1 oracle SQL (docs/phase2_rule_table_v3_1_reference.sql)
has no R07r; v3.4 splits v3.3's R07a groups into R07r + R07a. The
acceptance check folds R07r back into R07a (ORACLE_ALIASES) so the oracle
and the v3.3 counts still apply unchanged.
"""
from __future__ import annotations

import copy
from datetime import date

from rule_engine.seed_data import v3_1, v3_3

VERSION_LABEL = "v3.4"
SUPERSEDES = "v3.3"  # seeding may retire exactly this active version, nothing else
PAYMENT_MODULE_CODE = v3_3.PAYMENT_MODULE_CODE
EFFECTIVE_FROM = date(2026, 11, 11)  # owner decision date
# (source_document is 255 characters at most: v3.3's text plus a short suffix.)
SOURCE_DOCUMENT = v3_3.SOURCE_DOCUMENT + " + v3.4 decided 2026-11-11 (project owner)"
RULE_SET_NOTES = (
    "v3.4: R07r (Refund Reversal Confirmation, SSK = Remitter) is credited through the CBS credit file "
    "(rev file), with no SSKUPI reply; R06 replies RRC 501 and is closed by the system once the reply is in "
    "an SSKUPI file, the credit following when the confirmation arrives (owner 2026-11-11). "
    + v3_3.RULE_SET_NOTES
)

RESPONSE_CODES = v3_3.RESPONSE_CODES
RET_ALTERNATIVES = v3_3.RET_ALTERNATIVES
AGEING_SOURCE = v3_3.AGEING_SOURCE
AGEING_BUCKETS = v3_3.AGEING_BUCKETS

REFUND_REVERSAL_CONFIRMATION = "Refund Reversal Confirmation"  # the Adjustment report's Adjtype, exact

R07R = dict(
    code="R07r",
    name="Adjustment - Refund Reversal Confirmation (SSK = Remitter)",
    condition=v3_1._all(v3_1.ADJ_SSK, v3_1._f("adjustment.adjtype", "in", [REFUND_REVERSAL_CONFIRMATION])),
    status="MANUAL_CREDIT_REQUIRED",
    action=(
        "Credit the customer in CBS through the day's credit file (rev file). No SSKUPI reply: NPCI has already "
        "confirmed the refund reversal."
    ),
    priority="HIGH",
    tat_type="DAYS",
    tat_days=1,
    tat_day_basis="CALENDAR",
    source="Owner (2026-11-11, CBS credit file plan); ops' practice (2 Nov 2026 rev sheet)",
    notes=(
        "New in v3.4, taken out of R07a. On 2 Nov 2026, 22 of the 23 SSK-remitter Refund Reversal "
        "Confirmation rows are credited in ops' rev sheet. Adjtype match is exact and case-sensitive, as for "
        "R06 / R07a. The credit account is the Adjustment report's 'Remitter Account number'."
    ),
)

R06_STATUS = "RRC_REPLY_CREDIT_ON_CONFIRMATION"  # owner 2026-11-12, was MANUAL_CREDIT_REQUIRED
R06_ACTION = (
    "Reply to NPCI with flag RRC 501. The customer is credited when NPCI's Refund Reversal Confirmation "
    "arrives (R07r, through the credit file), not now. For RET: return on the same date and mark the colour "
    "in the working sheet."
)
R06_NOTE = (
    "v3.4 (owner 2026-11-11, ops' practice): the RRC 501 reply comes first and the credit follows the "
    "confirmation (R07r); the case is closed by the system once its reply is in an SSKUPI file. "
)


def _v3_4(rule: dict) -> dict:
    r = copy.deepcopy(rule)
    if r["code"] == "R06":
        r["status"] = R06_STATUS
        r["action"] = R06_ACTION
        r["notes"] = R06_NOTE + r.get("notes", "")
    return r


RULES = []
for _rule in v3_3.RULES:
    if _rule["code"] == "R07a":
        RULES.append(copy.deepcopy(R07R))
    RULES.append(_v3_4(_rule))
del _rule

# The oracle SQL and EXPECTED_COUNTS know no R07r: compare it as R07a.
ORACLE_ALIASES = {"R07r": "R07a"}
EXPECTED_COUNTS = v3_3.EXPECTED_COUNTS
EXPECTED_TOTALS = v3_3.EXPECTED_TOTALS
EXPECTED_COUNT_DATES = v3_3.EXPECTED_COUNT_DATES
