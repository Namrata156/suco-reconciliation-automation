"""S2: NTSL self-check arithmetic on synthetic cycles, passing and failing."""
from __future__ import annotations

from decimal import Decimal

from settlement.models import CheckStatus, NtslCycleCheck
from settlement.selfcheck import check_ntsl_date
from settlement.tests.base import AUTH_LINES, D, DC_LINES, NtslTestBase, replace


class SelfCheckTests(NtslTestBase):
    def run_check(self):
        summary = check_ntsl_date(self.module, D, source="test")
        return summary, {c.settlement_cycle.cycle_code: c for c in summary.checks}

    def test_balanced_auth_cycle_passes(self):
        self.load_cycle("1C", AUTH_LINES)
        _, checks = self.run_check()
        c = checks["1C"]
        self.assertEqual(c.status, CheckStatus.PASS, c.failures)
        self.assertEqual((c.computed_subtotal_debit, c.computed_subtotal_credit), (Decimal("1002.43"), Decimal("601.40")))
        self.assertEqual((c.ntsl_settlement_net, c.computed_settlement_net), (Decimal("-401.03"), Decimal("-401.03")))
        self.assertEqual((c.ntsl_final_net, c.computed_final_net), (Decimal("-401.03"), Decimal("-401.03")))
        self.assertEqual(c.final_diff, Decimal("0"))
        self.assertEqual(c.category_totals["TXN_REMITTER"], {"lines": 1, "txn_count": 3, "debit": "1000.00",
                                                             "credit": "0.00"})
        self.assertEqual(c.category_totals["MANDATE_FEE"]["credit"], "0.50")
        self.assertEqual(c.line_count, len(AUTH_LINES))

    def test_dispute_cycle_final_includes_net_adjusted_amount(self):
        self.load_cycle("DC1", DC_LINES)
        _, checks = self.run_check()
        self.assertEqual(checks["DC1"].status, CheckStatus.PASS, checks["DC1"].failures)
        self.assertEqual(checks["DC1"].ntsl_final_net, Decimal("435.00"))

    def test_post_settlement_lines_move_final(self):
        lines = replace(AUTH_LINES, "Net Adjusted Amount", credit="10.00")
        lines = replace(lines, "Final Settlement Amount", debit="391.03")
        self.load_cycle("2C", lines)
        _, checks = self.run_check()
        self.assertEqual(checks["2C"].status, CheckStatus.PASS, checks["2C"].failures)

    def test_subtotal_off_by_one_paisa_fails(self):
        self.load_cycle("1C", replace(AUTH_LINES, "Beneficiary / Remitter Sub Totals", credit="601.41"))
        _, checks = self.run_check()
        c = checks["1C"]
        self.assertEqual(c.status, CheckStatus.FAIL)
        self.assertEqual(c.subtotal_credit_diff, Decimal("-0.01"))
        self.assertEqual(c.subtotal_debit_diff, Decimal("0"))
        self.assertTrue(any(f.startswith("Sub Totals credit") for f in c.failures), c.failures)
        # Settlement Amount is checked against NTSL's own Sub Totals, so it also fails (401.03 vs 401.02).
        self.assertEqual(c.settlement_diff, Decimal("0.01"))

    def test_wrong_final_fails_only_the_final_step(self):
        self.load_cycle("1C", replace(AUTH_LINES, "Final Settlement Amount", debit="400.00"))
        _, checks = self.run_check()
        c = checks["1C"]
        self.assertEqual(c.status, CheckStatus.FAIL)
        self.assertEqual((c.subtotal_debit_diff, c.settlement_diff), (Decimal("0"), Decimal("0")))
        self.assertEqual(c.final_diff, Decimal("-1.03"))
        self.assertEqual(len(c.failures), 1)

    def test_unmapped_description_fails_and_is_listed(self):
        self.load_cycle("1C", AUTH_LINES + [("Remitter U9 Approved Brand New Charge", 1, "0", "0")])
        _, checks = self.run_check()
        c = checks["1C"]
        self.assertEqual(c.status, CheckStatus.FAIL)
        self.assertEqual(c.unmapped_count, 1)
        self.assertEqual(c.unmapped_lines[0]["description"], "Remitter U9 Approved Brand New Charge")
        self.assertIn("Remitter U9 Approved Brand New Charge", c.failures[0])

    def test_missing_subtotal_line_fails(self):
        self.load_cycle("1C", [ln for ln in AUTH_LINES if ln[0] != "Beneficiary / Remitter Sub Totals"])
        _, checks = self.run_check()
        c = checks["1C"]
        self.assertEqual(c.status, CheckStatus.FAIL)
        self.assertIsNone(c.ntsl_subtotal_debit)
        self.assertIn("Expected exactly one 'Sub Totals' line, found 0", c.failures)

    def test_final_line_must_be_the_one_the_loader_flagged(self):
        self.load_cycle("1C", AUTH_LINES, final_flag=False)
        _, checks = self.run_check()
        self.assertEqual(checks["1C"].status, CheckStatus.FAIL)

    def test_cycles_without_lines_are_missing_and_superseded_files_ignored(self):
        self.load_cycle("1C", AUTH_LINES)
        self.load_cycle("3C", replace(AUTH_LINES, "Final Settlement Amount", debit="1.00"), is_current=False)
        summary, checks = self.run_check()
        self.assertEqual(len(checks), 12)
        self.assertEqual(checks["1C"].status, CheckStatus.PASS)
        self.assertEqual(checks["3C"].status, CheckStatus.MISSING)
        self.assertFalse(summary.passed)

    def test_two_current_files_for_one_cycle_fail(self):
        self.load_cycle("1C", AUTH_LINES)
        self.load_cycle("1C", AUTH_LINES)
        _, checks = self.run_check()
        self.assertTrue(any("2 current NTSL files" in f for f in checks["1C"].failures))

    def test_rerun_supersedes_and_keeps_history(self):
        self.load_cycle("1C", AUTH_LINES)
        first = self.run_check()[1]["1C"]
        second = self.run_check()[1]["1C"]
        first.refresh_from_db()
        self.assertFalse(first.is_current)
        self.assertIsNotNone(first.superseded_at)
        self.assertTrue(second.is_current)
        self.assertEqual(NtslCycleCheck.objects.filter(business_date=D, settlement_cycle__cycle_code="1C").count(), 2)
        self.assertEqual(NtslCycleCheck.objects.filter(business_date=D, is_current=True).count(), 12)
