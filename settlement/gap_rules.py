"""
Settlement gap-explanation rules G01-G10 and Z, exactly as approved by the
project owner on 2026-10-11 (docs/phase4_gap_rules_draft_v1.txt, O1/O2/O4).
This module is the ONE place the approved rules are written down in code;
settlement.reconcile applies them. Nothing here reads the database.

  G01 Matched                     R09, the NPCI row the group points at
  G02 On-us second leg            R09 on-us, the payee (paired) NPCI row;
                                  beneficiary side
  G03 Settled, dispute rule       NPCI 00/RB row, CBS present, rule R04a /
                                  R04c / R06 / R07a
  G04 Timeout not credited        R05
  G05 NPCI-only success           R12
  G06 Remitter timeout reversed   R06b
  G07 DRC debited, not reversed   R02 (CBS side only)
  G08 CBS only                    R13 (CBS side only)
  G09 Dispute cycles              DC1/DC2 "Net Adjusted Amount" vs the signed
                                  money-moving Adjustment rows
  G10 Fees and taxes              NTSL-internal totals only (ops Q3 open)
  Z   No NTSL effect              R01, R03, R08, R10, R11, R07b/x/c and the
                                  DRC / RRC / TCC / complaint Adjustment rows:
                                  listed for audit, never attributed

G01-G03 count as SETTLED ("matched"), G04-G06 as EXPLAINED differences on
the NTSL side, G04-G08 as the explained items of the CBS-side SOP check (C1).

Anything the rules do not cover is NOT attributed: an NPCI row in NTSL's
population (code 00 / RB) with any other rule, a non-zero NTSL line in a
category no G rule covers, an Adjustment row whose type is not in the G09
sign table or whose Uid time cannot be read. Each shows up as an
UNEXPLAINED amount -- never a silent pass (owner's O1 note).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from settlement.categories import NtslCategory as C

# -- NPCI rows that NTSL settles -------------------------------------------
# NTSL's transaction lines equal NPCI's own rows with these response codes
# (Slice 1: exact per cycle x side x U2/U3 x RB). RB = deemed approved.
SUCCESS_CODE = "00"
DEEMED_APPROVED_CODE = "RB"
SETTLED_CODES = (SUCCESS_CODE, DEEMED_APPROVED_CODE)

# -- G rules -----------------------------------------------------------------
G01, G02, G03, G04, G05, G06, G07, G08, G09, G10 = (
    "G01", "G02", "G03", "G04", "G05", "G06", "G07", "G08", "G09", "G10",
)
UNATTRIBUTED = "UNATTRIBUTED"

G_LABELS = {
    G01: "Matched",
    G02: "On-us second leg",
    G03: "Settled, payment has a dispute rule",
    G04: "Timeout not credited",
    G05: "NPCI-only success",
    G06: "Remitter timeout already reversed in CBS",
    G07: "DRC debited, not reversed (CBS side)",
    G08: "CBS only (CBS side)",
    G09: "Dispute cycles (Adjustment file)",
    G10: "Fees and taxes (NTSL-internal)",
    UNATTRIBUTED: "Not attributed by any G rule",
}

MATCHED_RULE = "R09"  # G01 / G02
G03_RULES = ("R04a", "R04c", "R06", "R07a")
NPCI_EXPLAINED_RULES = {"R05": G04, "R12": G05, "R06b": G06}
CBS_EXPLAINED_RULES = {"R02": G07, "R13": G08}
SETTLED_G = (G01, G02, G03)
EXPLAINED_G = (G04, G05, G06)
# C1: the explained items of the CBS-side SOP check.
SOP_ITEM_RULES = {**NPCI_EXPLAINED_RULES, **CBS_EXPLAINED_RULES}
Z_RULES = ("R01", "R03", "R08", "R10", "R11", "R07b", "R07x", "R07c")

PRIMARY, PAIRED = "PRIMARY", "PAIRED"


def attribute_npci_row(*, role: str | None, rule_code: str | None, is_on_us: bool, cbs_present: bool) -> str:
    """G rule for one NPCI row that NTSL settles (code 00 / RB).

    role: PRIMARY when the row is the one its match group points at, PAIRED
    when it is the group's on-us second row, None when no current group
    holds it (a duplicate or an invalid RRN)."""
    if role == PRIMARY:
        if rule_code == MATCHED_RULE:
            return G01
        if rule_code in G03_RULES and cbs_present:
            return G03
        if rule_code in NPCI_EXPLAINED_RULES:
            return NPCI_EXPLAINED_RULES[rule_code]
    elif role == PAIRED and rule_code == MATCHED_RULE and is_on_us:
        return G02
    return UNATTRIBUTED


# -- sides ---------------------------------------------------------------------
REMITTER, BENEFICIARY = "REMITTER", "BENEFICIARY"


def side_of_npci_direction(direction: str) -> str | None:
    """NPCI DEBIT = SSK pays = NTSL remitter (debit); CREDIT = SSK receives =
    NTSL beneficiary (credit). Same pairing as reconciliation.presence."""
    return {"DEBIT": REMITTER, "CREDIT": BENEFICIARY}.get(direction)


# -- NTSL categories per comparison ------------------------------------------------
REMITTER_CATEGORIES = (C.TXN_REMITTER, C.TXN_REMITTER_RB)
BENEFICIARY_CATEGORIES = (C.TXN_BENEFICIARY, C.TXN_BENEFICIARY_RB)
TXN_CATEGORIES = REMITTER_CATEGORIES + BENEFICIARY_CATEGORIES
ADJUSTMENT_CATEGORIES = (C.ADJUSTMENT,)  # G09: "Net Adjusted Amount"
# G10: "all fee, GST, rebate and mandate-fee lines".
FEE_CATEGORIES = (
    C.FEE, C.FEE_GST, C.SWITCHING_FEE, C.SWITCHING_FEE_GST, C.PSP_FEE, C.PSP_FEE_GST,
    C.REBATE, C.REBATE_GST, C.MANDATE_FEE, C.MANDATE_FEE_GST, C.ADJUSTMENT_FEE, C.NRP_FEE, C.NRP_FEE_GST,
)
TOTAL_CATEGORIES = (C.SUBTOTAL, C.SETTLEMENT_AMOUNT, C.FINAL)  # checked by the S2 self-check
# No G rule covers these. All are 0.00 on every real file so far; a non-zero
# amount is reported as unexplained, never attributed.
UNCOVERED_CATEGORIES = tuple(
    c for c in C.values if c not in TXN_CATEGORIES + ADJUSTMENT_CATEGORIES + FEE_CATEGORIES + TOTAL_CATEGORIES
)

# -- G09: dispute cycles ------------------------------------------------------------
DC1, DC2 = "DC1", "DC2"
DISPUTE_CYCLES = (DC1, DC2)
DC_CUTOFF = time(16, 0)  # IST; inferred from real data, ops to confirm (P2)
IST = timezone(timedelta(hours=5, minutes=30))
_UID_EPOCH_MS_DIGITS = 13

PLUS, MINUS, NO_MONEY, UNKNOWN_TYPE = 1, -1, 0, None
_CREDIT_TYPES = ("credit adjustment", "online refund", "ret")
_NO_MONEY_TYPES = ("debit reversal confirmation", "refund reversal confirmation", "tcc")


def adjustment_sign(adjtype: str) -> int | None:
    """G09 sign table, for SSK as REMITTER (negated for SSK as beneficiary):
        +  Credit Adjustment, Online Refund, RET, Chargeback Raise
        -  Representment / Re-presentment Raise
        0  DRC, RRC, TCC, complaint rows (no money effect)
    Type families are read as the rule set reads them
    (rule_engine seed CHARGEBACK_TYPES and the R07b TAT rows): "chargeback
    raise" = contains 'chargeback' and 'raise'; representment = contains
    'presentment'. Any other type -> None (not in the table: never signed)."""
    t = " ".join((adjtype or "").lower().split())
    if "presentment" in t:
        return MINUS
    if t in _CREDIT_TYPES or ("chargeback" in t and "raise" in t):
        return PLUS
    if t in _NO_MONEY_TYPES or "complaint" in t:
        return NO_MONEY
    return UNKNOWN_TYPE


def signed_adjustment(adjtype: str, ssk_is_remitter: bool, amount):
    """Net effect for SSK (credit - debit, + = SSK receives), or None when the
    type is not in the sign table."""
    sign = adjustment_sign(adjtype)
    if sign is None:
        return None
    return amount * sign * (1 if ssk_is_remitter else -1)


def uid_time(uid: str) -> datetime | None:
    """The time the adjustment was raised, read from the Adjustment Uid: its
    last 13 digits are epoch milliseconds (inferred from real data -- every
    Uid of 14 Jul / 25 Sep / 26 Sep reads as a time on its own Adjdate; ops to
    confirm, P2). None when unreadable."""
    digits = (uid or "").strip().removeprefix("'")
    tail = digits[-_UID_EPOCH_MS_DIGITS:]
    if len(digits) < _UID_EPOCH_MS_DIGITS or not tail.isdigit():
        return None
    try:
        return datetime.fromtimestamp(int(tail) / 1000, IST)
    except (OverflowError, OSError, ValueError):
        return None


def dispute_cycle_for(raised_at: datetime | None, business_date: date) -> str | None:
    """DC1 = raised before 16:00 IST, DC2 = at or after. None (not assigned)
    when the time is unknown or not on the business date itself."""
    if raised_at is None or raised_at.date() != business_date:
        return None
    return DC1 if raised_at.time() < DC_CUTOFF else DC2
