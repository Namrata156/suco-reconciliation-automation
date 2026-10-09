"""
UPI business rule table v3.1 as plain data -- transcribed from
docs/phase2_rule_table_v3_approved.txt (v3 approved 2026-10-09, v3.1
amendment approved the same day by the project owner).

FROZEN. This module is the published content of version v3.1 and must
never be edited: a rule change is a new version, i.e. a new module
(v3_2.py, ...) plus a new seed. Because it is frozen and holds only plain
data (no imports from app code), both the `seed_rule_sets` command and the
data migration rule_engine/0002 read it, instead of the migration carrying
a second hand-copied table of 24 rules. rule_engine/tests/test_seed.py
checks it against the doc's own values.

Conditions follow rule_engine.conditions. Each one was checked against the
matching CASE branch of docs/phase2_rule_table_v3_1_reference.sql.
Places where the doc text needed interpreting carry an "INTERPRETATION:"
note in the rule's `notes`.

Codes are referenced by (flag, description): description is unique per
flag, and works for codes whose number is unknown.
"""
from __future__ import annotations

from datetime import date, time

VERSION_LABEL = "v3.1"
PAYMENT_MODULE_CODE = "UPI"
EFFECTIVE_FROM = date(2026, 10, 9)  # approval date of v3 and the v3.1 amendment
SOURCE_DOCUMENT = (
    "docs/phase2_rule_table_v3_approved.txt -- v3 approved 2026-10-09 + v3.1 amendment "
    "approved 2026-10-09 (project owner)"
)
RULE_SET_NOTES = (
    "Still to confirm with ops (Q8): the actual re-presentment response deadline and what it "
    "counts from; the 35-day limit in R07x / R07b's Re-presentment Raise TAT is unverified. "
    "Every final action still goes through Maker-Checker (Phase 3); rules only recommend."
)

# ---------------------------------------------------------------------------
# Response / reason codes
# ---------------------------------------------------------------------------
_SOP = "docs/sop_reference.md"
_OPS = "Ops note (docs/reconciliation_note_photo.jpeg, transcribed in docs/sop_reference.md)"
_RT = "docs/phase2_rule_table_v3_approved.txt"

RESPONSE_CODES = [
    # flag, code (None = unknown), description, source, notes
    ("DRC", "102", "Online reverse", f"{_OPS}; {_SOP} (DRC update)",
     "SOP wording: customer account reversed online."),
    ("DRC", "103", "Manually credited", f"{_OPS}; {_SOP} (DRC update)",
     "SOP wording: customer account reversed manually post-reconciliation."),
    ("DRC", "104", "Not effected to CBS", f"{_OPS}; {_SOP} (DRC update)",
     "SOP wording: online decline response failed."),
    ("TCC", "102", "Online credited", f"{_OPS}; {_SOP} (Timeout update)",
     "SOP wording: already credited online."),
    ("TCC", "103", "Credited manually post-reconciliation", f"{_SOP} (Timeout update); {_RT} Q4",
     "Not used by any v3.1 rule."),
    ("RET", "115", "Account doesn't exist", f"{_OPS}; {_RT} R05 / Q1",
     "R05 default; the maker may choose another RET reason."),
    ("RET", None, "Account closed", f"{_SOP} (Timeout update); {_RT} R05", "Numeric code not in the SOP or ops' note."),
    ("RET", None, "NRI account", f"{_SOP} (Timeout update); {_RT} R05", "Numeric code not in the SOP or ops' note."),
    ("RET", None, "Party instructions", f"{_SOP} (Timeout update); {_RT} R05", "Numeric code not in the SOP or ops' note."),
    ("RET", None, "Invalid beneficiary details", f"{_SOP} (Timeout update); {_RT} R05", "Numeric code not in the SOP or ops' note."),
    ("RET", None, "Credit freezed", f"{_SOP} (Timeout update); {_RT} R05", "Numeric code not in the SOP or ops' note."),
    ("RET", None, "Beneficiary bank unable to credit", f"{_SOP} (Timeout update); {_RT} R05", "Numeric code not in the SOP or ops' note."),
    ("RET", None, "Any other reason", f"{_SOP} (Timeout update); {_RT} R05", "Numeric code not in the SOP or ops' note."),
    ("RRC", "501", "Manually credited", f"{_OPS}; {_SOP} (Adjustment update)",
     "SOP wording: manually credited to customer post reconciliation."),
    ("NPCI_RESPONSE", "00", "Success", f"{_RT} (R09, R11, R12, R17)",
     "NPCI response code of a successful transaction."),
    ("NPCI_RESPONSE", "RB", "Deemed approved", f"{_RT} GLOSSARY / Q4; NPCI UPI Circular 17, OC 79 FY16-17",
     "Beneficiary bank timed out and NPCI settled the credit anyway. The remitter must NOT reverse "
     "its customer; the beneficiary bank confirms with TCC or returns with RET. No v3.1 condition "
     "tests RB directly: R04c catches these by Timeout side (doc: 'all NPCI code RB')."),
]

RET_ALTERNATIVES = [
    "Account closed", "NRI account", "Party instructions", "Invalid beneficiary details",
    "Credit freezed", "Beneficiary bank unable to credit", "Any other reason",
]

# ---------------------------------------------------------------------------
# Ageing buckets (doc AGEING, approved 2026-10-09)
# ---------------------------------------------------------------------------
AGEING_SOURCE = "Owner (Q5c / ageing), 2026-10-09; docs/phase2_rule_table_v3_approved.txt AGEING"
AGEING_BUCKETS = [
    # order, label, min_days, max_days, boundary_reason
    (1, "0-1 d", 0, 1, "T+1 : DRC / TCC / RET / reversal (RBI, NPCI)"),
    (2, "2-5 d", 2, 5, "T+5 : merchant payment reversal (RBI, UPI 4b)"),
    (3, "6-7 d", 6, 7, "7 d : chargeback raise (ops note)"),
    (4, "8-35 d", 8, 35, "35 d : chargeback representment (ops note)"),
    (5, "36-45 d", 36, 45, "45 d : customer chargeback window (NPCI OC 198)"),
    # INTERPRETATION: the doc's "45+ d" overlaps "36-45 d" at 45; read as 46 and over.
    (6, "45+ d", 46, None, "past every known deadline"),
]

# ---------------------------------------------------------------------------
# Condition helpers (build plain dicts)
# ---------------------------------------------------------------------------


def _f(fact, op, value):
    return {"fact": fact, "op": op, "value": value}


def _all(*nodes):
    return {"all": list(nodes)}


def _any(*nodes):
    return {"any": list(nodes)}


DRC_SSK = _f("drc.ssk_side", "eq", True)
TMO_SSK = _f("timeout.ssk_side", "eq", True)
ADJ_SSK = _f("adjustment.ssk_side", "eq", True)
CBS = _f("cbs.present", "eq", True)
NO_CBS = _f("cbs.present", "eq", False)
NPCI = _f("npci.present", "eq", True)
NO_NPCI = _f("npci.present", "eq", False)
REVERSED = _f("cbs_reversed", "eq", True)
NOT_REVERSED = _f("cbs_reversed", "eq", False)
CREDIT_TYPES = _f("adjustment.adjtype", "in", ["Credit Adjustment", "Online Refund", "RET"])
CONFIRMATION_TYPES = _f(
    "adjustment.adjtype", "in", ["Debit Reversal Confirmation", "Refund Reversal Confirmation", "TCC"]
)
CHARGEBACK_TYPES = _f("adjustment.adjtype", "icontains_any", ["chargeback", "presentment"])

T1 = dict(tat_type="DAYS", tat_days=1, tat_day_basis="WORKING")
NO_TAT = dict(tat_type="NONE", tat_days=None, tat_day_basis="")

_RBI_COMP = "RBI: Rs 100 per day compensation to the customer after T+1 (RBI/2019-20/67)."

# ---------------------------------------------------------------------------
# Rules, in evaluation order (first match wins). R04c sits AFTER the
# Adjustment rules on purpose: a RET that comes back for a remitter-side
# Timeout must still reach R06 (doc R04c note).
# ---------------------------------------------------------------------------
RULES = [
    dict(
        code="R01", name="DRC - debited and already reversed",
        condition=_all(DRC_SSK, CBS, REVERSED),
        status="NO_FINANCIAL_ACTION_REQUIRED", action="Update the DRC in the NPCI portal",
        priority="LOW", response_code=("DRC", "Online reverse"), **T1,
        source="SOP + Ops note; TAT: NPCI Circular 17, Owner (Q5)",
        notes="Ops: Eff=yes, rev=yes.",
    ),
    dict(
        code="R02", name="DRC - debited but NOT reversed",
        condition=_all(DRC_SSK, CBS),
        status="REVERSAL_REQUIRED", action="Reverse the customer manually, then update the DRC",
        priority="HIGH", response_code=("DRC", "Manually credited"), **T1,
        tat_note=_RBI_COMP, rbi_compensation_after_tat=True,
        source="SOP + Ops note; TAT: RBI/2019-20/67, Owner (Q5)",
        notes="Ops: Eff=yes, rev=#N/A. 'NOT reversed' is implied by order (R01 took the reversed ones).",
    ),
    dict(
        code="R03", name="DRC - never reached CBS",
        condition=_all(DRC_SSK),
        status="NO_FINANCIAL_ACTION_REQUIRED", action="Update the DRC in the NPCI portal",
        priority="MEDIUM", response_code=("DRC", "Not effected to CBS"), **T1,
        source="SOP + Ops note; TAT: Owner (Q5)",
        notes="Ops: Eff=#N/A, rev=#N/A. Customer was never debited. 'NOT in CBS' is implied by order.",
    ),
    dict(
        code="R04a", name="Timeout - credited in CBS",
        condition=_all(TMO_SSK, CBS, NOT_REVERSED),
        status="NO_FINANCIAL_ACTION_REQUIRED", action="Update TCC in the NPCI portal",
        priority="LOW", response_code=("TCC", "Online credited"), **T1,
        source="SOP + Ops note; TAT: NPCI Circular 17",
        notes="Ops: Eff=yes, rev=#N/A.",
    ),
    dict(
        code="R04b", name="Timeout - credited in CBS AND reversed",
        condition=_all(TMO_SSK, CBS),
        status="PENDING_INVESTIGATION", action="Check the customer's account first, then update TCC 102",
        priority="MEDIUM", response_code=("TCC", "Online credited"), **T1,
        source="Ops note",
        notes="Ops: Eff=yes, rev=yes. Code is TCC 102 after checking. 'reversed' is implied by order.",
    ),
    dict(
        code="R05", name="Timeout - NOT credited in CBS",
        condition=_all(TMO_SSK),
        status="RETURN_REQUIRED",
        action=(
            "Return to the remitter: RET with a reason. The system pre-fills 115. The maker may "
            "choose another SOP reason (account closed, NRI account, party instructions, invalid "
            "beneficiary details, credit freezed, beneficiary bank unable to credit, any other "
            "reason). The checker approves."
        ),
        priority="HIGH", response_code=("RET", "Account doesn't exist"),
        alternative_codes=[("RET", d) for d in RET_ALTERNATIVES],
        **T1, tat_cutoff_time=time(16, 0),
        tat_note="T+1, RET by 4 pm. Otherwise NPCI debits SSK. " + _RBI_COMP,
        rbi_compensation_after_tat=True,
        source="Ops note, Owner (Q1); TAT: NPCI OC 93, RBI/2019-20/67",
        notes="Ops: Eff=#N/A, rev=#N/A. RET 115 is the default and is editable by the maker.",
    ),
    dict(
        code="R06b", name="Adjustment - credit due, but CBS already shows the money back",
        condition=_all(ADJ_SSK, CREDIT_TYPES, CBS, REVERSED),
        status="PENDING_INVESTIGATION",
        action=(
            "Maker checks the customer's account. No automatic credit. The maker either credits and "
            "replies RRC 501, or replies RRC 501 without crediting. The checker approves."
        ),
        priority="HIGH", response_code=("RRC", "Manually credited"), **T1,
        reason_template=(
            "RET/refund received, but CBS already shows the reversal. Verify before crediting, or "
            "the customer is paid twice."
        ),
        source="Owner (Q7)",
        notes=(
            "New in v3.1. Phase 3: raise again in the Maker-Checker design -- can 'reply without "
            "crediting' become a standard, checker-approved outcome? "
            "INTERPRETATION: 'CBS already shows the reversal (debit + credit back)' = cbs.present "
            "and cbs_reversed, as in the reference SQL."
        ),
    ),
    dict(
        code="R06", name="Adjustment - Credit Adjustment, Online Refund or RET (SSK = Remitter)",
        condition=_all(ADJ_SSK, CREDIT_TYPES),
        status="MANUAL_CREDIT_REQUIRED",
        action=(
            "Credit the customer manually, then reply to NPCI with flag RRC. For RET: return on the "
            "same date and mark the colour in the working sheet."
        ),
        priority="HIGH", response_code=("RRC", "Manually credited"), **T1,
        source="SOP + Ops note",
        notes=(
            "A RET here can answer a remitter-side Timeout (code RB). 'and not R06b' is implied by "
            "order. Adjtype match is exact and case-sensitive, as in the reference SQL."
        ),
    ),
    dict(
        code="R07a", name="Adjustment - confirmation of an action already taken",
        condition=_all(ADJ_SSK, CONFIRMATION_TYPES),
        status="NO_FINANCIAL_ACTION_REQUIRED",
        action="None - this is NPCI's record of an action already taken",
        priority="LOW", **NO_TAT,
        source="Owner (Q2)",
    ),
    dict(
        code="R07x", name="Adjustment - chargeback older than 35 days",
        condition=_all(ADJ_SSK, CHARGEBACK_TYPES, _f("adjustment.age_days", "gt", 35)),
        status="EXCEPTION",
        action=(
            "Bring to the user's notice, at the top of the queue and in the report. Then handle as "
            "R07b (accept / represent / escalate)."
        ),
        priority="HIGH", **T1, tat_note="T+1 (first review)",
        reason_template=(
            "Dispute is {age_days} days past the original transaction (limit 35). Check whether the "
            "response deadline has already passed."
        ),
        source="Owner (Q8)",
        notes=(
            "New in v3.1. Age = Adjdate - Txndate (the ORIGINAL transaction date), so a stale "
            "dispute can't look fresh. The 35-day limit is from ops' note and is still to be "
            "confirmed with ops (Q8). Alerting is status + priority + reason only until Phase 3 / "
            "Phase 5. The doc's reason text says 'N days'; stored as the {age_days} placeholder. "
            "INTERPRETATION: 'chargeback / representment type' = Adjtype containing 'chargeback' or "
            "'presentment' (case-insensitive), as in the reference SQL."
        ),
    ),
    dict(
        code="R07b", name="Adjustment - chargeback",
        condition=_all(ADJ_SSK, CHARGEBACK_TYPES),
        status="CHARGEBACK_REQUIRED",
        action="Raise or respond to the chargeback (accept or represent) in the NPCI portal",
        priority="HIGH", tat_type="BY_ADJTYPE", tat_days=None, tat_day_basis="",
        tat_note=(
            "Customer's own window to raise a chargeback: 45 days (NPCI OC 198 FY23-24). Since 2025 "
            "NPCI decides chargebacks automatically from the beneficiary bank's TCC/RET (NPCI OC 213 "
            "FY24-25)."
        ),
        tat_rows=[
            # order, label, condition, days, basis, source, notes
            (1, "Wrong chargeback raise and representment",
             _all(_f("adjustment.adjtype", "icontains_any", ["wrong"]),
                  _f("adjustment.adjtype", "icontains_any", ["chargeback"])),
             35, "CALENDAR", "Ops note", ""),
            (2, "Fraud chargeback raise and representment",
             _all(_f("adjustment.adjtype", "icontains_any", ["fraud"]),
                  _f("adjustment.adjtype", "icontains_any", ["chargeback"])),
             35, "CALENDAR", "Ops note", ""),
            (3, "Deferred chargeback raise and representment",
             _all(_f("adjustment.adjtype", "icontains_any", ["deferred"]),
                  _f("adjustment.adjtype", "icontains_any", ["chargeback"])),
             1, "WORKING", "Ops note", "T+1"),
            (4, "Re-presentment Raise",
             _f("adjustment.adjtype", "icontains_any", ["re-presentment"]),
             35, "CALENDAR", "Owner (Q5c)",
             "UNVERIFIED - no public NPCI figure, confirm with ops (Q8)."),
            (5, "Chargeback raise",
             _all(_f("adjustment.adjtype", "icontains_any", ["chargeback"]),
                  _f("adjustment.adjtype", "icontains_any", ["raise"])),
             7, "CALENDAR", "Ops note",
             "Checked after rows 1-3 so 'Wrong/Fraud/Deferred ... Raise' get their own TAT."),
        ],
        source="Ops note, NPCI",
        notes=(
            "35 days old or less (otherwise R07x, by order). "
            "INTERPRETATION: the doc's TAT labels are categories, not exact Adjtype strings; each "
            "TAT row matches by keywords. A chargeback type matching none of them (e.g. a plain "
            "'Chargeback Representment') has no TAT in the doc."
        ),
    ),
    dict(
        code="R07c", name="Adjustment - complaint or any other type",
        condition=_all(ADJ_SSK),
        status="PENDING_INVESTIGATION", action="Manual review",
        priority="MEDIUM", **T1, tat_note="T+1 (first review)",
        source="Owner (Q2, Q5)",
        notes="Complaint types ('Complaint Raise', 'Response to Complaint') or any type not listed above.",
    ),
    dict(
        code="R04c", name="Timeout - SSK is the REMITTER",
        condition=_all(_f("timeout.ssk_side", "eq", False)),
        status="NO_FINANCIAL_ACTION_REQUIRED",
        action=(
            "None. Do NOT reverse the customer (code RB = deemed approved). Wait for the beneficiary "
            "bank's TCC or RET. A RET arrives later in the Adjustment file -> R06."
        ),
        priority="LOW", **NO_TAT,
        source="NPCI Circular 17, NPCI OC 213; Owner (Q2, Q4)",
        notes=(
            "Checked AFTER R06-R07c on purpose: remitter-side timeouts that already have a TCC go "
            "to R07a, those with a RET go to R06. ssk_side = False means 'SSK is the other party', "
            "not 'SSK not involved'."
        ),
    ),
    dict(
        code="R08", name="Dispute that is only the other bank's side",
        condition=_all(
            _any(_f("drc.ssk_side", "eq", False), _f("adjustment.ssk_side", "eq", False)),
            NO_CBS, NO_NPCI,
        ),
        status="NO_FINANCIAL_ACTION_REQUIRED", action="None - the other bank acts",
        priority="LOW", **NO_TAT,
        source="Owner (Q2)",
        notes=(
            "INTERPRETATION: 'SSK is not the side that acts' = a DRC or Adjustment row with "
            "ssk_side False (a Timeout with ssk_side False was already taken by R04c), as in the "
            "reference SQL. The doc gives no TAT."
        ),
    ),
    dict(
        code="R09", name="Matched",
        condition=_all(
            CBS, NPCI,
            _f("amount_match", "eq", True), _f("direction_match", "eq", True),
            _f("npci.response_code", "eq", "00"),
            NOT_REVERSED,
            _any(_f("is_on_us", "eq", False), _f("cbs.pair_missing", "eq", False)),
        ),
        status="RECONCILED", action="None", priority="NONE", **NO_TAT,
        source="Spec",
        notes=(
            "Includes on-us payments where both sides match. "
            "INTERPRETATION (from the reference SQL, not spelled out in the doc's When line): a CBS "
            "reversal pair is not 'matched' (it goes to R10), and an on-us payment with one CBS "
            "side missing is not 'matched' (it goes to R14)."
        ),
    ),
    dict(
        code="R10", name="Reversed in CBS but not in the DRC file",
        condition=_all(REVERSED),
        status="NO_FINANCIAL_ACTION_REQUIRED", action="None - the customer was refunded online",
        priority="LOW", **NO_TAT,
        source="Owner (Q2)",
        notes=(
            "INTERPRETATION: 'no DRC row for the RRN' comes from order -- every SSK-side DRC row was "
            "taken by R01-R03. As in the reference SQL, a DRC row where SSK is the other party does "
            "not stop R10 (0 such rows on the 3 loaded dates)."
        ),
    ),
    dict(
        code="R11", name="In NPCI only - declined",
        condition=_all(NPCI, NO_CBS, _f("npci.response_code", "ne", "00")),
        status="NO_FINANCIAL_ACTION_REQUIRED", action="None - no money moved",
        priority="NONE", **NO_TAT,
        source="Owner (Q2)",
    ),
    dict(
        code="R12", name="In NPCI only - successful",
        condition=_all(NPCI, NO_CBS),
        status="ADJUSTMENT_REQUIRED",
        action="Pass an adjustment entry in CBS (money moved, but the customer's account was not updated)",
        priority="HIGH", **T1,
        tat_note="RBI: Rs 100 per day compensation after T+1 (UPI 4a); T+5 for merchant payments (UPI 4b).",
        rbi_compensation_after_tat=True,
        source="Spec; TAT: RBI/2019-20/67",
        notes=(
            "'NPCI code is 00' is implied by order (R11 took the non-00 ones). "
            "INTERPRETATION: the T+5 merchant-payment TAT is kept as a note only. The doc does not "
            "say how a merchant payment is recognised in the matrix, so no TAT variant is stored."
        ),
    ),
    dict(
        code="R13", name="In CBS only",
        condition=_all(CBS, NO_NPCI),
        status="PENDING_INVESTIGATION",
        action=(
            "Re-check against the NEXT business date's NPCI files once they are loaded. Found there "
            "-> treat as matched (late-cycle transaction). Still missing -> escalate to Reversal "
            "Required (refund the customer), HIGH, T+1 from escalation."
        ),
        priority="MEDIUM", tat_type="NEXT_BUSINESS_DATE_LOAD", tat_days=None, tat_day_basis="",
        tat_note="Next business date's load. After escalation: Reversal Required, HIGH, T+1.",
        source="Owner (Q3)",
        notes="Priority Medium until escalated. The next-day re-check needs cross-date matching (Step 4).",
    ),
    dict(
        code="R14", name="On-us, one CBS side missing",
        condition=_all(_f("is_on_us", "eq", True), CBS, _f("cbs.pair_missing", "eq", True)),
        status="EXCEPTION", action="Manual investigation", priority="HIGH", **T1,
        source="Not given in the doc (no Source line)",
    ),
    dict(
        code="R15", name="Amount mismatch",
        condition=_all(CBS, NPCI, _f("amount_match", "eq", False)),
        status="EXCEPTION", action="Manual investigation", priority="HIGH", **T1,
        source="Spec",
    ),
    dict(
        code="R16", name="Direction mismatch",
        condition=_all(CBS, NPCI, _f("direction_match", "eq", False)),
        status="EXCEPTION", action="Manual investigation", priority="HIGH", **T1,
        source="Not given in the doc (no Source line)",
    ),
    dict(
        code="R17", name="In both, but NPCI code is not 00",
        condition=_all(CBS, NPCI, _f("npci.response_code", "ne", "00")),
        status="PENDING_INVESTIGATION", action="Manual review", priority="MEDIUM", **T1,
        source="Spec",
        notes="RB (deemed approved) cases are caught earlier by R04a / R04c / R06 / R07a.",
    ),
    dict(
        code="R99", name="Anything else",
        condition={"always": True},
        status="PENDING_INVESTIGATION", action="Manual review", priority="MEDIUM", **T1,
        source="Spec",
    ),
]

# Doc's SUMMARY counts (14 Jul / 25 Sep / 26 Sep), recomputed 2026-10-09, and
# reproduced by docs/phase2_rule_table_v3_1_reference.sql. The Step 4
# evaluator's acceptance test must reproduce them. Not stored in the DB.
EXPECTED_COUNTS = {
    "R01": (143, 32, 4), "R02": (6, 0, 0), "R03": (116, 6, 1), "R04a": (2, 0, 0),
    "R04b": (0, 0, 0), "R05": (18, 0, 0), "R06b": (0, 0, 1), "R06": (16, 28, 9),
    "R07a": (227, 44, 20), "R07x": (1, 0, 0), "R07b": (1, 1, 0), "R07c": (1, 0, 1),
    "R04c": (7, 0, 1), "R08": (4, 2, 1), "R09": (67992, 70958, 68560), "R10": (69, 143, 88),
    "R11": (8749, 7861, 7367), "R12": (1, 11, 0), "R13": (1, 10, 578), "R14": (0, 0, 0),
    "R15": (0, 0, 0), "R16": (0, 0, 0), "R17": (0, 0, 0), "R99": (0, 0, 0),
}
EXPECTED_TOTALS = (77354, 79096, 76631)
EXPECTED_COUNT_DATES = (date(2026, 7, 14), date(2026, 9, 25), date(2026, 9, 26))
