"""Q4 permissible difference: Rs 0 by default, held as data, changed only by
an Administrator through maker-checker (settlement.tolerance)."""
from __future__ import annotations

from decimal import Decimal

from exception_management.services import InvalidTransition, MissingInput, RoleRequired
from settlement.models import (
    MapStatus,
    ResultStatus,
    SettlementTolerance,
    SettlementToleranceEvent,
    ToleranceImmutableError,
)
from settlement.tests.recon_base import SettlementTestBase
from settlement.tolerance import (
    active_tolerance,
    approve_tolerance_change,
    propose_tolerance_change,
    reject_tolerance_change,
)


class ToleranceTests(SettlementTestBase):
    def setUp(self):
        super().setUp()
        self.admin = self.user("Administrator")
        self.checker = self.user("Checker")

    def test_default_is_rs_zero_seeded_once(self):
        t = active_tolerance(self.module)
        self.assertEqual((t.version, t.status, t.amount, t.txn_count), (1, MapStatus.ACTIVE, Decimal("0.00"), 0))
        self.assertEqual(active_tolerance(self.module).pk, t.pk)
        self.assertEqual(list(t.events.values_list("event_type", flat=True)), ["SEEDED"])

    def test_admin_proposes_checker_approves(self):
        p = propose_tolerance_change(self.module, self.admin, amount="1.00", txn_count=0, reason="ops: rounding")
        self.assertEqual((p.status, active_tolerance(self.module).amount), (MapStatus.PENDING, Decimal("0.00")))
        approve_tolerance_change(p, self.checker, remarks="ok")
        p.refresh_from_db()
        self.assertEqual((p.status, p.self_approved, p.decided_by_username), (MapStatus.ACTIVE, False,
                                                                                self.checker.username))
        self.assertEqual(active_tolerance(self.module).amount, Decimal("1.00"))
        self.assertEqual(SettlementTolerance.objects.get(version=1).status, MapStatus.RETIRED)
        self.assertEqual(list(SettlementToleranceEvent.objects.order_by("id").values_list("event_type", flat=True)),
                         ["SEEDED", "PROPOSED", "RETIRED", "APPROVED"])

    def test_only_administrator_proposes_only_checker_decides(self):
        maker = self.user("Maker")
        with self.assertRaises(RoleRequired):
            propose_tolerance_change(self.module, maker, amount="1", txn_count=0, reason="x")
        with self.assertRaises(RoleRequired):
            propose_tolerance_change(self.module, self.checker, amount="1", txn_count=0, reason="x")
        p = propose_tolerance_change(self.module, self.admin, amount="1", txn_count=0, reason="x")
        with self.assertRaises(RoleRequired):
            approve_tolerance_change(p, self.admin)
        with self.assertRaises(RoleRequired):
            reject_tolerance_change(p, maker, reason="no")

    def test_self_approval_only_with_both_roles_and_flagged(self):
        both = self.user("Administrator", "Checker")
        p = propose_tolerance_change(self.module, both, amount="2", txn_count=1, reason="x")
        approve_tolerance_change(p, both)
        p.refresh_from_db()
        self.assertTrue(p.self_approved)
        self.assertTrue(SettlementToleranceEvent.objects.get(tolerance=p, event_type="APPROVED").self_approved)

    def test_reject_needs_reason(self):
        p = propose_tolerance_change(self.module, self.admin, amount="1", txn_count=0, reason="x")
        with self.assertRaises(MissingInput):
            reject_tolerance_change(p, self.checker, reason=" ")
        reject_tolerance_change(p, self.checker, reason="not agreed")
        p.refresh_from_db()
        self.assertEqual(p.status, MapStatus.REJECTED)
        self.assertEqual(active_tolerance(self.module).amount, Decimal("0.00"))
        with self.assertRaises(InvalidTransition):
            approve_tolerance_change(p, self.checker)

    def test_invalid_proposals(self):
        for amount, n in (("-1", 0), ("abc", 0), ("1", -1), ("1", "1.5"), ("0", 0)):
            with self.assertRaises(MissingInput, msg=(amount, n)):
                propose_tolerance_change(self.module, self.admin, amount=amount, txn_count=n, reason="x")
        with self.assertRaises(MissingInput):
            propose_tolerance_change(self.module, self.admin, amount="1", txn_count=0, reason="")
        propose_tolerance_change(self.module, self.admin, amount="1", txn_count=0, reason="x")
        with self.assertRaises(InvalidTransition):  # one pending at a time
            propose_tolerance_change(self.module, self.admin, amount="2", txn_count=0, reason="y")

    def test_values_never_change_outside_the_workflow(self):
        t = active_tolerance(self.module)
        t.amount = Decimal("5")
        with self.assertRaises(ToleranceImmutableError):
            t.save()
        with self.assertRaises(ToleranceImmutableError):
            SettlementTolerance.objects.filter(pk=t.pk).update(amount=5)
        with self.assertRaises(ToleranceImmutableError):
            t.delete()
        with self.assertRaises(ToleranceImmutableError):
            SettlementTolerance.objects.create(payment_module=self.module, version=9, status=MapStatus.ACTIVE,
                                               amount=5, reason="x", proposed_at=t.proposed_at)
        with self.assertRaises(ToleranceImmutableError):
            t.events.first().delete()

    def test_approved_tolerance_applies_on_the_next_run(self):
        self.build_day(beneficiary_ntsl="750")  # 100 unexplained
        self.assertEqual(self.reconcile().result.status, ResultStatus.UNEXPLAINED)
        p = propose_tolerance_change(self.module, self.admin, amount="100", txn_count=0, reason="test")
        self.assertEqual(self.reconcile().result.status, ResultStatus.UNEXPLAINED)  # pending: not applied
        approve_tolerance_change(p, self.checker)
        r = self.reconcile().result
        self.assertEqual(r.status, ResultStatus.TALLIED)
        self.assertEqual((r.tolerance_id, r.tolerance_amount), (p.pk, Decimal("100.00")))
