"""
NTSL fee GST check (owner approved 2026-11-06): a WARNING-ONLY addition to
the S2 self-check (settlement.selfcheck). It never changes a cycle's
PASS / FAIL / MISSING status, its failures, settlement readiness or the
settlement result (settlement.reconcile); it is stored on its own fields,
NtslCycleCheck.gst_check / gst_warning.

What ops do by hand (docs/sop_reference.md -> "Ops' NTSL working sheet"):
total the fee lines (description has "fee", not "GST") per column, work out
GST at 18% and compare it with NTSL's own GST lines. The system does the
same per fee/GST category pair and per side (debit and credit separately):

    expected GST = round(fee x GST_RATE, 2)           (half up)
    difference   = expected GST - NTSL GST            (computed - NTSL, as
                                                       the rest of S2)
    tolerance    = GST_TOLERANCE (flat Rs 1.00 per pair and side)
    within       = |difference| <= tolerance, and not "GST with no fee"
                   (fee 0, NTSL GST not 0) or "fee with no GST" (expected
                   GST not 0, NTSL GST 0).

A fee too small to carry GST (expected rounds to 0.00) with 0.00 GST is
within. Zero fee and zero GST is within.

fee_txn_count (the fee category's category_totals txn_count, both sides
together, 0 if none) is shown for information only; it does not change the
tolerance.

Not checked (no GST category, see the 2026-11-06 report on the real files):
ADJUSTMENT_FEE ("... Fee with Tax": GST is inside the line),
SETTLEMENT_CHARGES (no GST line exists) and PENALTY ("Including GST").
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from settlement.categories import NtslCategory as C

# Owner, 2026-11-06: GST on NPCI fees is 18%.
GST_RATE = Decimal("0.18")
# PM decision 2026-11-06 at owner's request: flat Rs 1; real data max diff
# Rs 0.04 over 5 dates / 299 rows. Also matches CGST Act s.170 (tax payable
# rounded to the nearest rupee). Widening it is a control change.
GST_TOLERANCE = Decimal("1.00")

GST_PAIRS: tuple[tuple[str, str], ...] = (
    (C.FEE, C.FEE_GST),
    (C.SWITCHING_FEE, C.SWITCHING_FEE_GST),
    (C.PSP_FEE, C.PSP_FEE_GST),
    (C.REBATE, C.REBATE_GST),
    (C.MANDATE_FEE, C.MANDATE_FEE_GST),
    (C.NRP_FEE, C.NRP_FEE_GST),
)
SIDES = ("debit", "credit")

PAISA = Decimal("0.01")
ZERO = Decimal("0.00")

NOTE_GST_WITHOUT_FEE = "GST_WITHOUT_FEE"
NOTE_FEE_WITHOUT_GST = "FEE_WITHOUT_GST"
NOTE_OUTSIDE_TOLERANCE = "OUTSIDE_TOLERANCE"


def _m(v: Decimal) -> str:
    return f"{v:.2f}"


def expected_gst(fee: Decimal) -> Decimal:
    return (fee * GST_RATE).quantize(PAISA, rounding=ROUND_HALF_UP)


def check_fee_gst(category_totals: dict) -> tuple[dict, bool]:
    """category_totals as built by settlement.selfcheck._evaluate (amounts may
    be Decimal or 2-decimal strings). Returns (gst_check, gst_warning)."""

    def amount(cat, side) -> Decimal:
        t = category_totals.get(cat)
        return Decimal(str(t[side])) if t else ZERO

    rows = []
    for fee_cat, gst_cat in GST_PAIRS:
        t = category_totals.get(fee_cat)
        txn_count = int(t.get("txn_count") or 0) if t else 0
        for side in SIDES:
            fee, ntsl = amount(fee_cat, side), amount(gst_cat, side)
            exp = expected_gst(fee)
            diff = exp - ntsl
            if fee == ZERO and ntsl != ZERO:
                note = NOTE_GST_WITHOUT_FEE
            elif exp != ZERO and ntsl == ZERO:
                note = NOTE_FEE_WITHOUT_GST
            elif abs(diff) > GST_TOLERANCE:
                note = NOTE_OUTSIDE_TOLERANCE
            else:
                note = None
            rows.append({
                "fee_category": str(fee_cat), "gst_category": str(gst_cat), "side": side.upper(),
                "fee": _m(fee), "fee_txn_count": txn_count, "expected_gst": _m(exp), "ntsl_gst": _m(ntsl),
                "difference": _m(diff), "tolerance": _m(GST_TOLERANCE), "within": note is None, "note": note,
            })
    warning = any(not r["within"] for r in rows)
    return {
        "rate": str(GST_RATE),
        "tolerance": _m(GST_TOLERANCE),
        "rows": rows,
    }, warning
