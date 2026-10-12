"""The approved G-rule table (settlement.gap_rules) as pure functions."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from settlement import gap_rules as g
from settlement.categories import NtslCategory
from settlement.tests.recon_base import uid_at

D = date(2026, 7, 14)


class AttributionTests(SimpleTestCase):
    def a(self, role, rule, on_us=False, cbs=True):
        return g.attribute_npci_row(role=role, rule_code=rule, is_on_us=on_us, cbs_present=cbs)

    def test_g01_matched_primary(self):
        self.assertEqual(self.a(g.PRIMARY, "R09"), g.G01)

    def test_g02_only_paired_row_of_on_us_r09(self):
        self.assertEqual(self.a(g.PAIRED, "R09", on_us=True), g.G02)
        self.assertEqual(self.a(g.PAIRED, "R09", on_us=False), g.UNATTRIBUTED)
        self.assertEqual(self.a(g.PAIRED, "R06", on_us=True), g.UNATTRIBUTED)

    def test_g03_dispute_rules_need_cbs(self):
        for rule in ("R04a", "R04c", "R06", "R07a"):
            self.assertEqual(self.a(g.PRIMARY, rule), g.G03, rule)
            self.assertEqual(self.a(g.PRIMARY, rule, cbs=False), g.UNATTRIBUTED, rule)

    def test_g04_g05_g06(self):
        self.assertEqual(self.a(g.PRIMARY, "R05", cbs=False), g.G04)
        self.assertEqual(self.a(g.PRIMARY, "R12", cbs=False), g.G05)
        self.assertEqual(self.a(g.PRIMARY, "R06b"), g.G06)

    def test_anything_else_is_not_attributed(self):
        for rule in ("R10", "R11", "R13", "R15", "R17", "R99", "R07b", None):
            self.assertEqual(self.a(g.PRIMARY, rule), g.UNATTRIBUTED, rule)
        self.assertEqual(self.a(None, "R09"), g.UNATTRIBUTED)

    def test_sop_items_are_g04_to_g08(self):
        self.assertEqual(set(g.SOP_ITEM_RULES.values()), {g.G04, g.G05, g.G06, g.G07, g.G08})

    def test_categories_partition(self):
        covered = set(g.TXN_CATEGORIES + g.ADJUSTMENT_CATEGORIES + g.FEE_CATEGORIES + g.TOTAL_CATEGORIES
                      + g.UNCOVERED_CATEGORIES)
        self.assertEqual(covered, set(NtslCategory.values))
        self.assertIn(NtslCategory.PENALTY, g.UNCOVERED_CATEGORIES)
        self.assertNotIn(NtslCategory.ADJUSTMENT, g.UNCOVERED_CATEGORIES)


class G09Tests(SimpleTestCase):
    def test_sign_table(self):
        for t in ("Credit Adjustment", "Online Refund", "RET", "Chargeback Raise", "Wrong Credit Chargeback Raise"):
            self.assertEqual(g.adjustment_sign(t), g.PLUS, t)
        for t in ("Re-presentment Raise", "Representment Raise", "Fraud Chargeback Representment"):
            self.assertEqual(g.adjustment_sign(t), g.MINUS, t)
        for t in ("Debit Reversal Confirmation", "Refund Reversal Confirmation", "TCC", "Complaint Raise",
                  "Response to Complaint"):
            self.assertEqual(g.adjustment_sign(t), g.NO_MONEY, t)
        for t in ("Pre-Arbitration Raise", "", "Something New"):
            self.assertIsNone(g.adjustment_sign(t), t)

    def test_signed_by_ssk_side(self):
        self.assertEqual(g.signed_adjustment("Credit Adjustment", True, Decimal("35")), Decimal("35"))
        self.assertEqual(g.signed_adjustment("Credit Adjustment", False, Decimal("35")), Decimal("-35"))
        self.assertEqual(g.signed_adjustment("Fraud Chargeback Representment", True, Decimal("3600")),
                         Decimal("-3600"))
        self.assertIsNone(g.signed_adjustment("Something New", True, Decimal("1")))

    def test_uid_time_real_example(self):
        # A real 14 Jul Uid: ...1784031644347 ms = 2026-07-14 17:50:44 IST.
        t = g.uid_time("419898881784031644347")
        self.assertEqual((t.date(), t.hour, t.minute), (D, 17, 50))
        self.assertEqual(g.dispute_cycle_for(t, D), g.DC2)

    def test_cutoff_16_ist(self):
        self.assertEqual(g.dispute_cycle_for(g.uid_time(uid_at(D, 15, 59)), D), g.DC1)
        self.assertEqual(g.dispute_cycle_for(g.uid_time(uid_at(D, 16, 0)), D), g.DC2)

    def test_unreadable_or_other_date_is_unassigned(self):
        self.assertIsNone(g.uid_time("abc"))
        self.assertIsNone(g.uid_time("123"))
        self.assertIsNone(g.dispute_cycle_for(None, D))
        self.assertIsNone(g.dispute_cycle_for(g.uid_time(uid_at(date(2026, 7, 13), 10)), D))
