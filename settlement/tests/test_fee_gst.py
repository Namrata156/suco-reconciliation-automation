"""Fee GST check (owner 2026-11-06): GST at 18% per fee/GST pair and side,
warning only -- it never changes the self-check status, failures or the
settlement result."""
from __future__ import annotations

from decimal import Decimal

from django.test import SimpleTestCase

from settlement.fee_gst import (
    GST_PAIRS,
    GST_RATE,
    GST_TOLERANCE,
    NOTE_FEE_WITHOUT_GST,
    NOTE_GST_WITHOUT_FEE,
    NOTE_OUTSIDE_TOLERANCE,
    check_fee_gst,
    expected_gst,
)
from settlement.models import CheckStatus, ResultStatus
from settlement.selfcheck import check_ntsl_date
from settlement.tests.base import AUTH_LINES, D, DC_LINES, NtslTestBase
from settlement.tests.recon_base import SettlementTestBase


def totals(**cats):
    """totals(FEE=(txn_count, debit, credit), ...) in category_totals shape (string amounts)."""
    return {c: {"lines": 1, "txn_count": n, "debit": dr, "credit": cr} for c, (n, dr, cr) in cats.items()}


def row(check, fee_category, side):
    return next(r for r in check["rows"] if r["fee_category"] == fee_category and r["side"] == side)


class ConstantsTests(SimpleTestCase):
    def test_owner_and_pm_values(self):
        self.assertEqual(GST_RATE, Decimal("0.18"))
        self.assertEqual(GST_TOLERANCE, Decimal("1.00"))
        self.assertEqual(len(GST_PAIRS), 6)

    def test_expected_gst_rounds_half_up_to_the_paisa(self):
        self.assertEqual(expected_gst(Decimal("0.25")), Decimal("0.05"))  # 0.045 -> 0.05 (not banker's 0.04)
        self.assertEqual(expected_gst(Decimal("448.00")), Decimal("80.64"))
        self.assertEqual(expected_gst(Decimal("0.02")), Decimal("0.00"))


class CheckFeeGstTests(SimpleTestCase):
    def test_shape_twelve_rows_in_fixed_order(self):
        check, warning = check_fee_gst({})
        self.assertFalse(warning)
        self.assertEqual(set(check), {"rate", "tolerance", "rows"})
        self.assertEqual((check["rate"], check["tolerance"]), ("0.18", "1.00"))
        self.assertEqual([(r["fee_category"], r["side"]) for r in check["rows"]],
                         [(f, s) for f, _ in GST_PAIRS for s in ("DEBIT", "CREDIT")])
        self.assertTrue(all(r["within"] and r["note"] is None and r["fee"] == "0.00" for r in check["rows"]))
        self.assertEqual(set(check["rows"][0]), {"fee_category", "gst_category", "side", "fee", "fee_txn_count",
                                                 "expected_gst", "ntsl_gst", "difference", "tolerance", "within",
                                                 "note"})

    def test_real_shaped_cycle_is_within(self):
        # 14 Jul 2026 1C, from the real NTSL file.
        check, warning = check_fee_gst(totals(
            FEE=(6967, "448.00", "560.26"), FEE_GST=(1605, "80.65", "100.85"),
            SWITCHING_FEE=(4868, "564.56", "0.00"), SWITCHING_FEE_GST=(0, "101.64", "0.00"),
            REBATE=(0, "0.00", "203.31"), REBATE_GST=(0, "0.00", "36.59"),
        ))
        self.assertFalse(warning)
        r = row(check, "SWITCHING_FEE", "DEBIT")
        self.assertEqual((r["fee"], r["fee_txn_count"], r["expected_gst"], r["ntsl_gst"], r["difference"],
                          r["tolerance"], r["within"]),
                         ("564.56", 4868, "101.62", "101.64", "-0.02", "1.00", True))
        self.assertTrue(all(r["tolerance"] == "1.00" for r in check["rows"]))

    def test_sides_are_compared_separately(self):
        # Debit side off by +2, credit side off by -2: netting would hide both.
        check, warning = check_fee_gst(totals(FEE=(10, "100.00", "100.00"), FEE_GST=(0, "20.00", "16.00")))
        self.assertTrue(warning)
        dr, cr = row(check, "FEE", "DEBIT"), row(check, "FEE", "CREDIT")
        self.assertEqual((dr["difference"], dr["within"], dr["note"]), ("-2.00", False, NOTE_OUTSIDE_TOLERANCE))
        self.assertEqual((cr["difference"], cr["within"], cr["note"]), ("2.00", False, NOTE_OUTSIDE_TOLERANCE))

    def test_boundary_is_inclusive(self):
        check, warning = check_fee_gst(totals(PSP_FEE=(0, "100.00", "0"), PSP_FEE_GST=(0, "17.00", "0")))
        self.assertEqual(row(check, "PSP_FEE", "DEBIT")["difference"], "1.00")
        self.assertFalse(warning)
        check, warning = check_fee_gst(totals(PSP_FEE=(0, "100.00", "0"), PSP_FEE_GST=(0, "16.99", "0")))
        self.assertTrue(warning)

    def test_tolerance_is_flat_whatever_the_fee_count(self):
        # PM decision 2026-11-06: flat Rs 1, no per-transaction widening.
        lines = totals(SWITCHING_FEE=(1000, "1000.00", "0"), SWITCHING_FEE_GST=(0, "181.01", "0"))
        check, warning = check_fee_gst(lines)  # expected 180.00, difference -1.01
        self.assertTrue(warning)
        r = row(check, "SWITCHING_FEE", "DEBIT")
        self.assertEqual((r["fee_txn_count"], r["difference"], r["tolerance"], r["note"]),
                         (1000, "-1.01", "1.00", NOTE_OUTSIDE_TOLERANCE))

    def test_gst_without_fee_is_a_warning_even_when_small(self):
        check, warning = check_fee_gst(totals(MANDATE_FEE_GST=(0, "0", "0.09")))
        r = row(check, "MANDATE_FEE", "CREDIT")
        self.assertTrue(warning)
        self.assertEqual((r["fee"], r["ntsl_gst"], r["within"], r["note"]), ("0.00", "0.09", False, NOTE_GST_WITHOUT_FEE))
        self.assertEqual(r["fee_txn_count"], 0)

    def test_fee_without_gst_is_a_warning_even_when_small(self):
        check, warning = check_fee_gst(totals(REBATE=(0, "0", "0.50")))
        r = row(check, "REBATE", "CREDIT")
        self.assertTrue(warning)
        self.assertEqual((r["expected_gst"], r["ntsl_gst"], r["within"], r["note"]), ("0.09", "0.00", False,
                                                                                       NOTE_FEE_WITHOUT_GST))

    def test_fee_too_small_for_gst_with_no_gst_is_within(self):
        check, warning = check_fee_gst(totals(FEE=(1, "0.02", "0")))
        self.assertFalse(warning)
        self.assertEqual(row(check, "FEE", "DEBIT")["expected_gst"], "0.00")

    def test_decimal_amounts_accepted(self):
        check, warning = check_fee_gst({"NRP_FEE": {"txn_count": 2, "debit": Decimal("10.00"), "credit": Decimal(0)},
                                        "NRP_FEE_GST": {"txn_count": 0, "debit": Decimal("1.80"), "credit": Decimal(0)}})
        self.assertFalse(warning)
        self.assertEqual(row(check, "NRP_FEE", "DEBIT")["expected_gst"], "1.80")


# A balanced cycle whose fee GST is all exactly 18%.
GST_OK_LINES = [
    ("Remitter U3 Approved Fee", 3, "1.00", "0"),
    ("Remitter U3 Approved Fee Gst", 3, "0.18", "0"),
    ("Remitter U3 Approved Transaction Amount", 3, "1000.00", "0"),
    ("Remitter U3 Approved NPCI Switching Fee", 3, "0.30", "0"),
    ("Remitter U3 Approved NPCI Switching Fee Gst", 0, "0.05", "0"),
    ("Beneficiary U3 Approved Transaction Amount", 2, "0", "600.00"),
    ("Settlement Charges", None, "0", "0"),
    ("Beneficiary / Remitter Sub Totals", None, "1001.53", "600.00"),
    ("Settlement Amount", None, "401.53", "0"),
    ("Net Adjusted Amount", None, "0", "0"),
    ("Final Settlement Amount", None, "401.53", "0"),
]


def _with(lines, description, debit):
    """Change one line's debit and the totals below it by the same amount, so the cycle still balances."""
    out, delta = [], Decimal(0)
    for d, n, dr, cr in lines:
        if d == description:
            delta = Decimal(debit) - Decimal(dr)
            dr = debit
        elif d in ("Beneficiary / Remitter Sub Totals", "Settlement Amount", "Final Settlement Amount"):
            dr = str(Decimal(dr) + delta)
        out.append((d, n, dr, cr))
    return out


class SelfCheckGstTests(NtslTestBase):
    def run_check(self):
        return {c.settlement_cycle.cycle_code: c for c in check_ntsl_date(self.module, D, source="test").checks}

    def test_all_gst_correct_no_warning(self):
        self.load_cycle("1C", GST_OK_LINES)
        c = self.run_check()["1C"]
        self.assertEqual(c.status, CheckStatus.PASS, c.failures)
        self.assertFalse(c.gst_warning)
        self.assertEqual(len(c.gst_check["rows"]), 12)
        self.assertEqual(row(c.gst_check, "SWITCHING_FEE", "DEBIT")["fee_txn_count"], 3)

    def test_gst_outside_tolerance_warns_but_cycle_still_passes(self):
        self.load_cycle("1C", _with(GST_OK_LINES, "Remitter U3 Approved NPCI Switching Fee Gst", "2.05"))
        c = self.run_check()["1C"]
        self.assertEqual(c.status, CheckStatus.PASS, c.failures)
        self.assertEqual(c.failures, [])
        self.assertTrue(c.gst_warning)
        r = row(c.gst_check, "SWITCHING_FEE", "DEBIT")
        self.assertEqual((r["expected_gst"], r["ntsl_gst"], r["difference"], r["note"]),
                         ("0.05", "2.05", "-2.00", NOTE_OUTSIDE_TOLERANCE))

    def test_missing_gst_lines_warn_without_failing(self):
        # AUTH_LINES has fees with no GST line (PSP, beneficiary fee, rebate, mandate fee).
        self.load_cycle("1C", AUTH_LINES)
        c = self.run_check()["1C"]
        self.assertEqual((c.status, c.failures), (CheckStatus.PASS, []))
        self.assertTrue(c.gst_warning)
        self.assertEqual({(r["fee_category"], r["side"]) for r in c.gst_check["rows"] if not r["within"]},
                         {("PSP_FEE", "DEBIT"), ("FEE", "CREDIT"), ("REBATE", "CREDIT"), ("MANDATE_FEE", "CREDIT")})

    def test_dispute_cycle_has_no_fees_and_no_warning(self):
        self.load_cycle("DC1", DC_LINES)
        c = self.run_check()["DC1"]
        self.assertFalse(c.gst_warning)
        self.assertTrue(all(r["within"] for r in c.gst_check["rows"]))

    def test_missing_cycle_is_not_checked(self):
        self.load_cycle("1C", GST_OK_LINES)
        c = self.run_check()["2C"]
        self.assertEqual(c.status, CheckStatus.MISSING)
        self.assertEqual((c.gst_check, c.gst_warning), ({}, False))


class GstWarningDoesNotTouchSettlementTests(SettlementTestBase):
    def test_tallied_date_stays_tallied_with_gst_warnings(self):
        # recon_base.ntsl_lines carries a switching fee with no GST line: every cycle with remitter
        # transactions gets a GST warning. The result must still be TALLIED.
        self.build_day()
        outcome = self.reconcile()
        self.assertEqual(outcome.result.status, ResultStatus.TALLIED, outcome.result.reasons)
        checks = {c.settlement_cycle.cycle_code: c for c in outcome.result.payment_module.ntsl_cycle_checks.filter(
            business_date=D, is_current=True)}
        self.assertTrue(all(c.status == CheckStatus.PASS for c in checks.values()))
        self.assertTrue(checks["1C"].gst_warning)
        self.assertEqual(row(checks["1C"].gst_check, "SWITCHING_FEE", "DEBIT")["note"], NOTE_FEE_WITHOUT_GST)
