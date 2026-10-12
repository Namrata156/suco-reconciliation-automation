"""Settlement reconciliation end to end (settlement.reconcile) on the base
day of recon_base: real presence matrix + rule evaluator + gap rules."""
from __future__ import annotations

from decimal import Decimal

from core.models import Direction, Notification, NotificationStatus
from exception_management.models import CaseType, ExceptionCase
from reconciliation.presence import build_presence_matrix
from settlement.models import (
    CheckStatus,
    NtslCycleCheck,
    ResultStatus,
    SettlementCase,
    SettlementCycleResult,
    SettlementResult,
    Side,
    ToleranceImmutableError,
)
from settlement.tests.recon_base import D, SettlementTestBase, ntsl_lines


def row(result, cycle, side) -> SettlementCycleResult:
    return result.cycle_rows.get(settlement_cycle__cycle_code=cycle, side=side)


class TalliedTests(SettlementTestBase):
    def setUp(self):
        super().setUp()
        self.build_day()

    def test_base_day_is_tallied_with_no_case(self):
        outcome = self.reconcile()
        r = outcome.result
        self.assertEqual(r.status, ResultStatus.TALLIED, r.reasons)
        self.assertEqual(r.reasons, [])
        self.assertEqual((r.unexplained_amount, r.unexplained_count), (Decimal("0.00"), 0))
        self.assertEqual(outcome.case_action, "none")
        self.assertFalse(ExceptionCase.objects.filter(case_type=CaseType.SETTLEMENT).exists())

    def test_cycle_side_attribution(self):
        r = self.reconcile().result
        rem = row(r, "1C", Side.REMITTER)
        self.assertEqual((rem.ntsl_amount, rem.ntsl_count), (Decimal("1070.00"), 3))
        self.assertEqual((rem.matched_amount, rem.matched_count), (Decimal("1050.00"), 2))  # G01 A + C
        self.assertEqual((rem.explained_amount, rem.explained_count), (Decimal("20.00"), 1))  # G05 D
        self.assertEqual(rem.detail["by_rule"]["G05"], {"count": 1, "amount": "20.00"})
        ben = row(r, "1C", Side.BENEFICIARY)
        self.assertEqual((ben.matched_amount, ben.matched_count), (Decimal("650.00"), 2))
        self.assertEqual(ben.detail["by_rule"]["G02"], {"count": 1, "amount": "50.00"})  # on-us second leg
        dc1 = row(r, "DC1", Side.ADJUSTMENT)
        self.assertEqual((dc1.ntsl_amount, dc1.ntsl_count, dc1.explained_amount, dc1.explained_count),
                         (Decimal("35.00"), None, Decimal("35.00"), 1))
        self.assertTrue(all(c.within_tolerance for c in r.cycle_rows.all()))

    def test_sop_check_explained_by_g05_and_g08(self):
        r = self.reconcile().result
        self.assertEqual(r.sop_cbs_net, Decimal("405.00"))
        self.assertEqual(r.sop_ntsl_net, Decimal("420.00"))
        self.assertEqual(r.sop_difference, Decimal("-15.00"))
        self.assertEqual(r.sop_explained, Decimal("-15.00"))
        self.assertEqual(r.sop_unexplained, Decimal("0.00"))
        by_rule = r.snapshot["sop"]["explained_by_rule"]
        self.assertEqual(by_rule["G05"], {"count": 1, "amount": "-20.00"})
        self.assertEqual(by_rule["G08"], {"count": 1, "amount": "5.00"})

    def test_snapshot_lists_contributing_z_and_g09(self):
        s = self.reconcile().result.snapshot
        contributing = {(c[0], c[1]) for c in s["contributing"]}
        self.assertEqual(contributing, {("100000000003", "G02"), ("100000000004", "G05")})
        z_rules = {(z[0], z[1]) for z in s["z"]["items"]}
        self.assertIn(("100000000005", "R11"), z_rules)
        self.assertEqual(s["npci_not_settled_by_code"], {"U9": 1})
        self.assertEqual([(x[0], x[6], x[7]) for x in s["g09"]["rows"]], [("100000000008", "DC1", "35.00")])
        self.assertEqual(s["g09"]["no_money_rows"], {"Debit Reversal Confirmation": 1})
        self.assertEqual(s["tolerance"], {"version": 1, "amount": "0.00", "txn_count": 0})

    def test_rerun_supersedes_never_deletes(self):
        first = self.reconcile().result
        second = self.reconcile().result
        first.refresh_from_db()
        self.assertFalse(first.is_current)
        self.assertIsNotNone(first.superseded_at)
        self.assertTrue(second.is_current)
        self.assertEqual(SettlementResult.objects.count(), 2)
        with self.assertRaises(ToleranceImmutableError):
            second.cycle_rows.first().delete()

    def test_selfcheck_runs_first(self):
        self.reconcile()
        self.assertEqual(NtslCycleCheck.objects.filter(is_current=True, business_date=D).count(), 12)
        self.assertFalse(NtslCycleCheck.objects.filter(is_current=True).exclude(status=CheckStatus.PASS).exists())


class ForcedDifferenceTests(SettlementTestBase):
    """A forced difference creates exactly one date-level case and an alert."""

    def setUp(self):
        super().setUp()
        self.build_day(beneficiary_ntsl="750")  # NTSL credits SSK 100 more than the system can name
        self.maker = self.user("Maker")
        self.checker = self.user("Checker")
        self.auditor = self.user("Auditor")

    def test_unexplained_creates_one_case_and_alert(self):
        with self.captureOnCommitCallbacks(execute=True):
            outcome = self.reconcile()
        r = outcome.result
        self.assertEqual(r.status, ResultStatus.UNEXPLAINED)
        ben = row(r, "1C", Side.BENEFICIARY)
        self.assertEqual((ben.unexplained_amount, ben.unexplained_count, ben.within_tolerance),
                         (Decimal("100.00"), 0, False))
        self.assertEqual(r.sop_unexplained, Decimal("100.00"))  # (405 - 320) - (-15)
        self.assertEqual(outcome.case_action, "created")
        cases = ExceptionCase.objects.filter(case_type=CaseType.SETTLEMENT)
        self.assertEqual(cases.count(), 1)
        case = cases.get()
        self.assertEqual((case.business_date, case.rrn, case.priority, case.status), (D, "", "HIGH", "OPEN"))
        self.assertIsNone(case.rule_result_id)
        self.assertEqual(case.settlement_case.result_id, r.pk)
        self.assertEqual(list(case.events.values_list("event_type", flat=True)), ["CREATED"])
        alerts = Notification.objects.filter(alert_type="SETTLEMENT_CASE")
        self.assertEqual(sorted(alerts.values_list("recipient_username", flat=True)),
                         sorted([self.maker.username, self.checker.username]))
        self.assertTrue(all(a.status == NotificationStatus.HELD_NO_MAIL_SERVER for a in alerts))
        self.assertIn("1C BENEFICIARY: Rs 100.00", alerts.first().body)

    def test_rerun_same_figures_keeps_one_case_and_no_new_alert(self):
        with self.captureOnCommitCallbacks(execute=True):
            first = self.reconcile()
        with self.captureOnCommitCallbacks(execute=True):
            second = self.reconcile()
        self.assertEqual(second.case_action, "unchanged")
        self.assertEqual(ExceptionCase.objects.filter(case_type=CaseType.SETTLEMENT).count(), 1)
        self.assertEqual(SettlementCase.objects.get().result_id, second.result.pk)
        self.assertEqual(Notification.objects.filter(alert_type="SETTLEMENT_CASE").count(), 2)  # first run only
        self.assertEqual(first.case_id, second.case_id)

    def test_rerun_that_tallies_flags_the_case_never_deletes_it(self):
        with self.captureOnCommitCallbacks(execute=True):
            first = self.reconcile()
        self.load_ntsl("1C", ntsl_lines("1070", 3, "650", 2))  # corrected NTSL file
        with self.captureOnCommitCallbacks(execute=True):
            second = self.reconcile()
        self.assertEqual(second.result.status, ResultStatus.TALLIED)
        self.assertEqual(second.case_action, "flagged")
        case = ExceptionCase.objects.get(pk=first.case_id)
        self.assertEqual(case.status, "OPEN")
        self.assertTrue(case.needs_review)
        self.assertIn("now TALLIED", case.needs_review_reason)
        self.assertEqual(list(case.events.values_list("event_type", flat=True)), ["CREATED", "FLAGGED_FOR_REVIEW"])
        self.assertEqual(case.settlement_case.result_id, second.result.pk)
        self.assertEqual(case.settlement_case.first_result_id, first.result.pk)
        review = Notification.objects.filter(alert_type="SETTLEMENT_REVIEW")
        self.assertEqual(list(review.values_list("recipient_username", flat=True)), [self.checker.username])

    def test_count_difference_alone_is_unexplained(self):
        self.load_ntsl("1C", ntsl_lines("1070", 3, "650", 3))  # one more beneficiary txn, same money
        r = self.reconcile().result
        self.assertEqual(r.status, ResultStatus.UNEXPLAINED)
        self.assertEqual(row(r, "1C", Side.BENEFICIARY).unexplained_count, 1)

    def test_dispute_cycle_difference(self):
        self.load_ntsl("1C", ntsl_lines("1070", 3, "650", 2))
        self.load_ntsl("DC1", ntsl_lines(adjustment="40"))
        r = self.reconcile().result
        self.assertEqual(r.status, ResultStatus.UNEXPLAINED)
        self.assertEqual(row(r, "DC1", Side.ADJUSTMENT).unexplained_amount, Decimal("5.00"))


class NotReadyTests(SettlementTestBase):
    def setUp(self):
        super().setUp()
        self.build_day()

    def test_matrix_rebuilt_after_evaluation(self):
        self.npci("100000000099", Direction.DEBIT, "1")
        build_presence_matrix(self.module, D)
        outcome = self.reconcile()
        self.assertEqual(outcome.result.status, ResultStatus.NOT_READY)
        self.assertTrue(any("rule result" in r.lower() or "evaluat" in r for r in outcome.result.reasons),
                        outcome.result.reasons)
        self.assertFalse(outcome.result.cycle_rows.exists())
        self.assertFalse(ExceptionCase.objects.filter(case_type=CaseType.SETTLEMENT).exists())

    def test_selfcheck_failure_o4(self):
        self.load_ntsl("1C", ntsl_lines("1070", 3, "650", 2, switching_n=2))
        r = self.reconcile().result
        self.assertEqual(r.status, ResultStatus.NOT_READY)
        self.assertTrue(any("1C" in x and "O4" in x for x in r.reasons), r.reasons)

    def test_no_rule_results(self):
        self.npci("100000000098", Direction.DEBIT, "1", d=D.replace(day=15))
        self.load_all_ntsl(d=D.replace(day=15))
        build_presence_matrix(self.module, D.replace(day=15))
        r = self.reconcile(D.replace(day=15)).result
        self.assertEqual(r.status, ResultStatus.NOT_READY)
        self.assertTrue(any("Rule results missing" in x for x in r.reasons), r.reasons)

    def test_not_ready_leaves_an_existing_case_alone(self):
        self.load_ntsl("1C", ntsl_lines("1070", 3, "750", 2))
        created = self.reconcile()
        self.assertEqual(created.case_action, "created")
        self.load_ntsl("1C", ntsl_lines("1070", 3, "650", 2, switching_n=1))
        outcome = self.reconcile()
        self.assertEqual((outcome.result.status, outcome.case_action), (ResultStatus.NOT_READY, "not_ready"))
        case = ExceptionCase.objects.get(pk=created.case_id)
        self.assertFalse(case.needs_review)
        self.assertEqual(case.settlement_case.result_id, created.result.pk)


class UncoveredAndUnplacedTests(SettlementTestBase):
    def test_nonzero_line_no_g_rule_covers_is_unexplained(self):
        self.build_day()
        # a penalty line in the POST section: Final = Settlement + Net Adjusted + penalty
        lines = ntsl_lines(adjustment="0")
        lines.insert(-1, ("Pre-Arbitration Deemed Acceptance Penalty", None, Decimal("12.00"), Decimal("0")))
        lines[-1] = ("Final Settlement Amount", None, Decimal("12.00"), Decimal("0"))
        self.load_ntsl("DC2", lines)
        r = self.reconcile().result
        self.assertEqual(r.status, ResultStatus.UNEXPLAINED, r.reasons)
        un = row(r, "DC2", Side.UNCOVERED)
        self.assertEqual(un.unexplained_amount, Decimal("-12.00"))

    def test_adjustment_type_not_in_sign_table_is_not_attributed(self):
        self.build_day()
        self.adjustment("100000000010", "Pre-Arbitration Raise", "9", raised=(17, 0))
        self.evaluate()
        r = self.reconcile().result
        self.assertEqual(r.status, ResultStatus.TALLIED)  # NTSL DC2 has 0, the row is listed, not signed
        self.assertEqual([x[0] for x in r.snapshot["g09"]["not_in_sign_table"]], ["100000000010"])

    def test_unreadable_uid_is_unassigned_and_shows_as_difference(self):
        self.build_day()
        self.adjustment("100000000011", "Online Refund", "4", raised=None)
        self.evaluate()
        self.load_ntsl("DC2", ntsl_lines(adjustment="4"))
        r = self.reconcile().result
        self.assertEqual(r.status, ResultStatus.UNEXPLAINED)
        self.assertEqual(row(r, "DC2", Side.ADJUSTMENT).unexplained_amount, Decimal("4.00"))
        self.assertEqual([x[0] for x in r.snapshot["g09"]["unassigned"]], ["100000000011"])
