"""Settlement cases in the Phase 3 queue (S5): maker-checker, audit, never
bulk, untouched by the Phase 3 sync, visible in the case admin and API."""
from __future__ import annotations

from django.db import IntegrityError, transaction
from django.urls import reverse

from exception_management import services
from exception_management.actions import ActionType
from exception_management.models import (
    CaseEvent,
    CaseImmutableError,
    CaseStatus,
    CaseType,
    Decision,
    ExceptionCase,
)
from exception_management.sync import sync_cases_for_date
from settlement.tests.recon_base import D, SettlementTestBase, ntsl_lines


class SettlementCaseTests(SettlementTestBase):
    def setUp(self):
        super().setUp()
        self.build_day(beneficiary_ntsl="750")
        self.maker = self.user("Maker")
        self.checker = self.user("Checker")
        self.outcome = self.reconcile()
        self.case = ExceptionCase.objects.get(pk=self.outcome.case_id)

    def explain(self, case=None, remarks="NTSL credit of Rs 100 is a late on-us leg; ops confirmed"):
        case = case or self.case
        services.take(case, self.maker)
        return services.submit(case, self.maker, remarks=remarks)

    def test_maker_explains_checker_accepts(self):
        action = self.explain()
        self.assertEqual(action.action_type, ActionType.SETTLEMENT_EXPLANATION)
        self.assertIsNone(action.rule_result_id)
        services.approve(self.case, self.checker, remarks="accepted")
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, CaseStatus.COMPLETED)  # nothing to carry out in the system
        events = list(self.case.events.values_list("event_type", flat=True))
        self.assertEqual(events, ["CREATED", "TAKEN", "SUBMITTED", "APPROVED", "COMPLETED"])
        submitted = self.case.events.get(event_type="SUBMITTED")
        self.assertEqual(submitted.changes, {"settlement_result_id": [None, self.outcome.result.pk]})
        self.assertEqual(submitted.rrn, "")
        with self.assertRaises(CaseImmutableError):
            submitted.delete()

    def test_explanation_required_and_only_settlement_action(self):
        services.take(self.case, self.maker)
        with self.assertRaises(services.MissingInput):
            services.submit(self.case, self.maker, remarks="")
        with self.assertRaises(services.MissingInput):
            services.submit(self.case, self.maker, action_type=ActionType.CLOSE_NO_ACTION, remarks="x")

    def test_transaction_case_cannot_take_the_settlement_action(self):
        tx = ExceptionCase.objects.filter(case_type=CaseType.TRANSACTION).first()
        if tx is None:  # the base day's R12 / R06 results need action; sync makes their cases
            sync_cases_for_date(self.module, D, source="test")
            tx = ExceptionCase.objects.filter(case_type=CaseType.TRANSACTION).first()
        services.take(tx, self.maker)
        with self.assertRaises(services.MissingInput):
            services.submit(tx, self.maker, action_type=ActionType.SETTLEMENT_EXPLANATION, remarks="x")

    def test_never_bulk(self):
        with self.assertRaises(services.BulkRefused):
            services.bulk_take([self.case.pk], self.maker)
        self.explain()
        with self.assertRaises(services.BulkRefused):
            services.bulk_approve([self.case.pk], self.checker)
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, CaseStatus.PENDING_APPROVAL)

    def test_phase3_sync_leaves_settlement_case_alone(self):
        summary = sync_cases_for_date(self.module, D, source="test")
        self.case.refresh_from_db()
        self.assertEqual((self.case.status, self.case.needs_review), (CaseStatus.OPEN, False))
        self.assertEqual(summary.superseded, 0)
        self.assertNotIn("/OPEN", summary.cases_by_rule_status)  # settlement cases are not counted as rules
        self.assertEqual(self.case.events.count(), 1)

    def test_changed_figures_withdraw_a_pending_explanation(self):
        action = self.explain()
        self.load_ntsl("1C", ntsl_lines("1070", 3, "760", 2))  # now 110 unexplained
        outcome = self.reconcile()
        self.assertEqual(outcome.case_action, "updated")
        self.case.refresh_from_db()
        action.refresh_from_db()
        self.assertEqual((self.case.status, action.decision), (CaseStatus.IN_PROGRESS, Decision.WITHDRAWN))
        ev = self.case.events.get(event_type="RESULT_UPDATED")
        self.assertEqual(ev.changes["settlement_result_id"], [self.outcome.result.pk, outcome.result.pk])

    def test_changed_figures_after_acceptance_flag_review(self):
        self.explain()
        services.approve(self.case, self.checker)
        self.load_ntsl("1C", ntsl_lines("1070", 3, "760", 2))
        self.assertEqual(self.reconcile().case_action, "flagged")
        self.case.refresh_from_db()
        self.assertEqual((self.case.status, self.case.needs_review), (CaseStatus.COMPLETED, True))
        services.clear_review(self.case, self.checker, remarks="re-explained offline")
        self.case.refresh_from_db()
        self.assertFalse(self.case.needs_review)

    def test_one_settlement_case_per_date_enforced_by_db(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExceptionCase.objects.create(payment_module=self.module, business_date=D, case_type=CaseType.SETTLEMENT,
                                         rrn="", rule_code="", status_category="EXCEPTION", priority="HIGH")

    def test_case_type_keys_enforced_by_db(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExceptionCase.objects.create(payment_module=self.module, business_date=D.replace(day=20),
                                         case_type=CaseType.TRANSACTION, rrn="123456789012", rule_code="R01",
                                         status_category="EXCEPTION", priority="HIGH")

    # -- screens ---------------------------------------------------------------------------
    def test_admin_queue_and_detail(self):
        self.client.force_login(self.maker)
        r = self.client.get(reverse("admin:exception_management_exceptioncase_changelist") + "?case_type=SETTLEMENT")
        self.assertContains(r, "Settlement (NTSL)")
        r = self.client.get(reverse("admin:exception_management_exceptioncase_change", args=[self.case.pk]))
        self.assertContains(r, "1C")
        self.assertContains(r, "UNEXPLAINED")
        self.assertContains(r, "settlement case: no rule result")
        r = self.client.get(reverse("admin:exception_management_exceptioncase_transition",
                                    args=[self.case.pk, "take"]))
        self.assertEqual(r.status_code, 200)

    def test_api_list_and_detail(self):
        self.client.force_login(self.checker)
        r = self.client.get("/api/exceptions/cases/?case_type=SETTLEMENT")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([c["id"] for c in r.json()["results"]], [self.case.pk])
        self.assertTrue(r.json()["results"][0]["flags"]["settlement"])
        d = self.client.get(f"/api/exceptions/cases/{self.case.pk}/").json()
        self.assertIsNone(d["rule_result"])
        self.assertEqual(d["overlay_rows"], [])
        self.assertEqual(d["settlement"]["status"], "UNEXPLAINED")
        ben = [c for c in d["settlement"]["cycles"] if c["cycle"] == "1C" and c["side"] == "BENEFICIARY"][0]
        self.assertEqual(ben["unexplained_amount"], "100.00")

    def test_api_submit_and_approve(self):
        self.client.force_login(self.maker)
        self.assertEqual(self.client.post(f"/api/exceptions/cases/{self.case.pk}/take/", {}).status_code, 200)
        r = self.client.post(f"/api/exceptions/cases/{self.case.pk}/submit/",
                             {"action_type": ActionType.SETTLEMENT_EXPLANATION, "remarks": "explained"})
        self.assertEqual(r.status_code, 200, r.content)
        self.client.force_login(self.checker)
        r = self.client.post("/api/exceptions/cases/bulk/approve/", {"case_ids": [self.case.pk]},
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)
        r = self.client.post(f"/api/exceptions/cases/{self.case.pk}/approve/", {})
        self.assertEqual(r.json()["status"], CaseStatus.COMPLETED)
        self.assertEqual(CaseEvent.objects.filter(case=self.case, event_type="APPROVED").count(), 1)
