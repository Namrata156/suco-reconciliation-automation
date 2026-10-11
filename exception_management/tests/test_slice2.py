"""Slice 2: Amendment 1 (A1-A5), the P5 previous-day-R12 hint, available
transitions per role, bulk (P2, all or nothing) and response-code change
requests (P3)."""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction

from exception_management import code_changes, services
from exception_management.actions import ActionType
from exception_management.forms import SubmitForm
from exception_management.models import (
    CaseEvent,
    CaseImmutableError,
    CaseStatus,
    ChangeRequestStatus,
    Decision,
    EventType,
    ExceptionCase,
    ProposedAction,
    ResponseCodeChangeEvent,
)
from exception_management.roles import Role
from exception_management.services import BulkRefused, InvalidTransition, MissingInput, RoleRequired
from exception_management.tests.base import D, CaseTestBase
from core.models import RegisterType
from rule_engine.models import CodeFlag, ResponseCode, RuleResult, RuleSetImmutableError, seeding_response_codes
from rule_engine.tests.test_evaluator import EvaluatorTestBase
from transactions.models.staging import StagingAdjustment


class AdjustmentRowsMixin:
    def adjustment_row(self, rrn, d, adjtype, txn_date="01-06-2026"):
        """A current Adjustment row for rrn, file date d (Adjdate), whose
        original date is not loaded."""
        if not hasattr(self, "_files"):
            self._files, self._row = {}, 0
        f = EvaluatorTestBase.file(self, RegisterType.ADJUSTMENT, d)
        self._row += 1
        staging = StagingAdjustment.objects.create(
            uploaded_file=f, row_number=self._row, row_reference=f":{self._row}",
            raw_cells={"4": adjtype, "5": "SSK", "8": txn_date}, txn_date_raw=txn_date,
        )
        return EvaluatorTestBase.fact(self, RegisterType.ADJUSTMENT, rrn, d, staging_row_id=staging.id, uploaded_file=f)


# ---------------------------------------------------------------------------
class RrcAlreadySentTests(AdjustmentRowsMixin, CaseTestBase):  # A1
    RRN = "100000000001"

    def test_warning_from_a_row_on_another_date(self):
        self.adjustment_row(self.RRN, D, "Credit Adjustment")
        self.adjustment_row(self.RRN, D + timedelta(days=2), "Refund Reversal Confirmation")
        self.adjustment_row(self.RRN, D + timedelta(days=1), "refund reversal confirmation ")  # earliest; case/space
        self.result(self.RRN, "R06")
        self.result("100000000002", "R06")
        s = self.sync()
        c = self.case(self.RRN)
        self.assertEqual(c.rrc_already_sent_on, D + timedelta(days=1))
        self.assertEqual(c.rrc_warning, "RRC already sent for this RRN on 2026-07-15; check before crediting")
        self.assertIsNone(self.case("100000000002").rrc_already_sent_on)
        self.assertEqual(s.rrc_already_sent, 1)
        self.assertEqual(c.events.get().changes["rrc_already_sent_on"], [None, "2026-07-15"])
        self.assertEqual(self.sync().events_written, 0)  # idempotent

    def test_row_arriving_later_updates_cases_of_every_date(self):
        self.result(self.RRN, "R06")
        self.sync()
        self.adjustment_row(self.RRN, D + timedelta(days=1), "Refund Reversal Confirmation")
        s = self.sync(D + timedelta(days=5))  # any date's sync refreshes the warning
        self.assertEqual(s.rrc_warning_changed, 1)
        c = self.case(self.RRN)
        self.assertEqual(c.rrc_already_sent_on, D + timedelta(days=1))
        e = c.events.get(event_type=EventType.WARNINGS_CHANGED)
        self.assertEqual(e.changes, {"rrc_already_sent_on": [None, "2026-07-15"]})

    def test_maker_closes_with_the_warning_as_evidence(self):
        self.adjustment_row(self.RRN, D + timedelta(days=1), "Refund Reversal Confirmation")
        c = self.make_case(self.RRN, "R06")
        m = self.maker()
        services.take(c, m)
        form = SubmitForm(case=c, user=m)
        self.assertEqual(form.initial["evidence_notes"], c.rrc_warning)
        self.assertEqual(form.initial["action_type"], ActionType.MANUAL_CREDIT_RRC_501)  # no rule change, no auto
        a = services.submit(c, m, action_type=ActionType.CLOSE_NO_ACTION, remarks="RRC already sent on 15 Jul",
                            evidence_notes=c.rrc_warning)
        services.approve(c, self.checker())
        a.refresh_from_db()
        self.assertEqual((self.case(self.RRN).status, a.decision), (CaseStatus.COMPLETED, Decision.APPROVED))


class AmendmentTests(CaseTestBase):
    def test_a2_chargeback_represent(self):
        c = self.make_case("100000000001", "R07b")
        self.assertEqual(c.default_action_type, "")  # R07b: maker chooses (accept or represent)
        m = self.maker()
        services.take(c, m)
        a = services.submit(c, m, action_type=ActionType.CHARGEBACK_REPRESENT)
        self.assertEqual((a.action_type, a.response_code), ("CHARGEBACK_REPRESENT", None))

    def test_a3_close_without_action_completes_on_approval(self):
        c = self.make_case("100000000001", "R13")
        m, chk = self.maker(), self.checker()
        services.take(c, m)
        services.submit(c, m, action_type=ActionType.CLOSE_NO_ACTION, remarks="Found in next day's NPCI file")
        a = services.approve(c, chk)
        c.refresh_from_db()
        self.assertEqual(c.status, CaseStatus.COMPLETED)
        self.assertEqual((a.external_reference, a.completed_by, a.completed_at), ("", chk, a.decided_at))
        self.assertEqual(
            list(c.events.filter(event_type__in=["APPROVED", "COMPLETED"]).values_list("event_type", "old_status",
                                                                                      "new_status")),
            [("APPROVED", "PENDING_APPROVAL", "APPROVED"), ("COMPLETED", "APPROVED", "COMPLETED")],
        )
        with self.assertRaises(InvalidTransition):
            services.mark_completed(c, m, external_reference="x")

    def test_other_actions_still_need_mark_done(self):
        c = self.make_case("100000000001", "R01")
        m = self.maker()
        services.take(c, m)
        services.submit(c, m)
        services.approve(c, self.checker())
        self.assertEqual(self.case(c.rrn).status, CaseStatus.APPROVED)

    def test_a4_no_deadline_case(self):
        self.result("100000000001", "R07b", due=None, tat_unresolved=True)
        s = self.sync()
        self.assertEqual((s.created, s.no_deadline), (1, 1))
        c = ExceptionCase.objects.queue().get()
        self.assertEqual((c.no_deadline, c.due_date, c.needs_attention), (True, None, True))
        self.rerun({"100000000001": "R07b"}, **{"100000000001": {"due": D + timedelta(days=7)}})
        self.sync()
        c = self.case("100000000001")
        self.assertFalse(c.no_deadline)
        e = c.events.get(event_type=EventType.RESULT_UPDATED)
        self.assertEqual(e.changes["no_deadline"], [True, False])

    def _flagged(self):
        c = self.make_case("100000000001", "R01")
        m = self.maker()
        services.take(c, m)
        services.submit(c, m)
        services.approve(c, self.checker())
        self.rerun({"100000000001": "R02"})
        self.sync()
        return self.case("100000000001")

    def test_a5_checker_clears_review_with_remark(self):
        c = self._flagged()
        self.assertTrue(c.needs_review)
        chk = self.checker()
        with self.assertRaises(RoleRequired):
            services.clear_review(c, self.maker(), remarks="fine")
        with self.assertRaises(MissingInput):
            services.clear_review(c, chk, remarks="  ")
        reason = c.needs_review_reason
        e = services.clear_review(c, chk, remarks="DRC 102 already sent; R02 change is a data correction")
        c.refresh_from_db()
        self.assertEqual((c.needs_review, c.needs_review_reason, c.status), (False, "", CaseStatus.APPROVED))
        self.assertEqual((e.event_type, e.actor, e.changes["needs_review_reason"]), ("REVIEW_CLEARED", chk, [reason, ""]))
        with self.assertRaises(InvalidTransition):
            services.clear_review(c, chk, remarks="again")


class PreviousDayHintTests(CaseTestBase):  # P5
    def hint(self, rrn, d=D):
        return ExceptionCase.objects.with_previous_day_r12().get(rrn=rrn, business_date=d).previous_day_r12

    def test_hint(self):
        prev = D - timedelta(days=1)
        self.result("100000000001", "R12", prev)
        self.result("100000000001", "R13", D)
        self.result("100000000002", "R12", D - timedelta(days=2))
        self.result("100000000002", "R13", D)
        self.result("100000000003", "R12", prev)
        self.result("100000000003", "R01", D)  # not R13: no hint
        self.sync(prev)
        self.sync(D)
        self.assertTrue(self.hint("100000000001"))
        self.assertFalse(self.hint("100000000002"))
        self.assertFalse(self.hint("100000000003"))
        self.assertFalse(self.hint("100000000001", prev))
        self.assertEqual(ExceptionCase.objects.filter(business_date=D).count(), 3)  # display only, nothing linked

    def test_hint_follows_reevaluation_of_previous_day(self):
        prev = D - timedelta(days=1)
        self.result("100000000001", "R12", prev)
        self.result("100000000001", "R13", D)
        self.sync(D)
        self.assertTrue(self.hint("100000000001"))
        self.rerun({"100000000001": "R09"}, prev)  # D-1 re-evaluated: no longer R12
        self.assertFalse(self.hint("100000000001"))


class AvailableTransitionsTests(CaseTestBase):
    def test_per_role_and_status(self):
        c = self.make_case()
        m, chk, admin, aud = self.maker(), self.checker(), self.user(Role.ADMINISTRATOR), self.user(Role.AUDITOR)
        at = services.available_transitions
        self.assertEqual(at(c, m), ["take", "remark"])
        self.assertEqual(at(c, chk), [])
        self.assertEqual(at(c, admin), ["reassign"])
        self.assertEqual(at(c, aud), [])
        services.take(c, m)
        c.refresh_from_db()
        self.assertEqual(at(c, m), ["release", "submit", "remark"])
        self.assertEqual(at(c, self.maker()), ["remark"])
        services.submit(c, m)
        c.refresh_from_db()
        self.assertEqual(at(c, chk), ["approve", "reject"])
        both = self.user(Role.MAKER, Role.CHECKER)
        self.assertEqual(at(c, both), ["approve", "reject", "remark"])


# ---------------------------------------------------------------------------
class BulkTests(AdjustmentRowsMixin, CaseTestBase):  # P2
    def cases(self, codes, maker=None):
        out = []
        for i, code in enumerate(codes):
            out.append(self.make_case(f"40000000000{i}", code))
        if maker:
            services.bulk_take(out, maker)
        return [self.case(c.rrn) for c in out]

    def snapshot(self):
        return (list(ExceptionCase.objects.order_by("pk").values_list("status", "assigned_to_id")),
                ProposedAction.objects.count(), CaseEvent.objects.count())

    def test_bulk_take_submit_approve(self):
        m, chk = self.maker(), self.checker()
        cs = self.cases(["R01", "R01", "R01"], maker=m)
        self.assertTrue(all(c.status == CaseStatus.IN_PROGRESS and c.assigned_to == m for c in cs))
        actions = services.bulk_submit(cs, m, remarks="DRC 102 batch")
        self.assertEqual({a.action_type for a in actions}, {"DRC_102"})
        approved = services.bulk_approve(cs, chk)
        self.assertEqual(len(approved), 3)
        for c in cs:
            c.refresh_from_db()
            self.assertEqual(c.status, CaseStatus.APPROVED)
            self.assertEqual(c.events.filter(event_type__in=["SUBMITTED", "APPROVED"]).count(), 2)  # own events

    def test_any_high_refuses_everything(self):
        m = self.maker()
        cs = self.cases(["R01", "R03", "R05"], maker=m)  # R05 is HIGH
        before = self.snapshot()
        with self.assertRaisesRegex(BulkRefused, "LOW/MEDIUM"):
            services.bulk_submit(cs, m, action_type=ActionType.CLOSE_NO_ACTION, remarks="x")
        self.assertEqual(self.snapshot(), before)

    def test_different_prefilled_actions_need_one_choice(self):
        m = self.maker()
        cs = self.cases(["R01", "R03"], maker=m)  # DRC 102 vs DRC 104
        with self.assertRaisesRegex(BulkRefused, "pre-filled"):
            services.bulk_submit(cs, m)

    def test_failure_on_a_later_case_rolls_back_earlier_ones(self):
        m = self.maker()
        cs = self.cases(["R01", "R01"], maker=m)
        services.release(cs[1], m)  # second case is no longer taken -> submit fails on it
        before = self.snapshot()
        with self.assertRaises(InvalidTransition), transaction.atomic():
            services.bulk_submit(cs, m)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.case(cs[0].rrn).status, CaseStatus.IN_PROGRESS)

    def test_bulk_approve_needs_one_shared_action(self):
        m, chk = self.maker(), self.checker()
        cs = self.cases(["R01", "R01"], maker=m)  # (R13 would be refused as Pending Investigation, B3)
        services.submit(cs[0], m, action_type=ActionType.DRC_102)
        services.submit(cs[1], m, action_type=ActionType.DRC_103)
        before = self.snapshot()
        with self.assertRaisesRegex(BulkRefused, "differ"):
            services.bulk_approve(cs, chk)
        self.assertEqual(self.snapshot(), before)

    def test_bulk_approve_refuses_not_pending(self):
        m, chk = self.maker(), self.checker()
        cs = self.cases(["R01", "R01"], maker=m)
        services.submit(cs[0], m)
        with self.assertRaisesRegex(BulkRefused, "not pending"):
            services.bulk_approve(cs, chk)

    def test_cases_with_warnings_are_handled_one_by_one(self):
        self.adjustment_row("400000000000", D + timedelta(days=1), "Refund Reversal Confirmation")
        m = self.maker()
        cs = self.cases(["R01", "R01"], maker=m)
        with self.assertRaisesRegex(BulkRefused, "warning"):
            services.bulk_submit(cs, m)

    def test_bulk_self_approval_flagged_per_case(self):
        both = self.user(Role.MAKER, Role.CHECKER)
        cs = self.cases(["R01", "R01"], maker=both)
        services.bulk_submit(cs, both)
        self.assertTrue(all(a.self_approved for a in services.bulk_approve(cs, both)))

    def test_roles(self):
        m = self.maker()
        cs = self.cases(["R01"], maker=m)
        with self.assertRaises(RoleRequired):
            services.bulk_submit(cs, self.checker())
        with self.assertRaises(RoleRequired):
            services.bulk_take([self.make_case("499999999999").pk], self.checker())


# ---------------------------------------------------------------------------
class CodeChangeTests(CaseTestBase):  # P3
    def setUp(self):
        super().setUp()
        self.m, self.chk = self.maker(), self.checker()
        self.closed = ResponseCode.objects.get(payment_module=self.module, flag=CodeFlag.RET,
                                               description="Account closed")  # used by active R05, code unknown

    def test_fill_in_unknown_code(self):
        with self.assertRaises(RoleRequired):
            code_changes.propose_code_change(self.closed, self.chk, new_code="114", reason="NPCI list")
        req = code_changes.propose_code_change(self.closed, self.m, new_code=" 114 ", reason="NPCI RET reason list")
        self.assertEqual((req.status, req.old_code, req.new_code), (ChangeRequestStatus.PENDING, None, "114"))
        self.closed.refresh_from_db()
        self.assertIsNone(self.closed.code)  # nothing applied yet
        with self.assertRaises(RoleRequired):
            code_changes.approve_code_change(req, self.m)
        code_changes.approve_code_change(req, self.chk, remarks="checked")
        self.closed.refresh_from_db()
        req.refresh_from_db()
        self.assertEqual((self.closed.code, req.status, req.self_approved), ("114", "APPROVED", False))
        self.assertEqual(list(req.events.values_list("event_type", "actor_username")),
                         [("PROPOSED", self.m.username), ("APPROVED", self.chk.username)])
        self.assertEqual(req.events.last().changes, {"code": [None, "114"]})

    def test_locked_code_only_fills_unknown_code(self):
        ret115 = ResponseCode.objects.get(payment_module=self.module, flag=CodeFlag.RET, code="115")
        with self.assertRaisesRegex(MissingInput, "only its unknown numeric code"):
            code_changes.propose_code_change(ret115, self.m, new_code="116", reason="x")
        with self.assertRaisesRegex(MissingInput, "only its unknown numeric code"):
            code_changes.propose_code_change(self.closed, self.m, new_description="Closed account", reason="x")
        with self.assertRaisesRegex(MissingInput, "already used"):
            code_changes.propose_code_change(self.closed, self.m, new_code="115", reason="x")
        with self.assertRaises(MissingInput):
            code_changes.propose_code_change(self.closed, self.m, new_code="114", reason="  ")
        with self.assertRaisesRegex(MissingInput, "changes nothing"):
            code_changes.propose_code_change(ret115, self.m, reason="x")

    def test_one_pending_request_per_code_and_reject(self):
        req = code_changes.propose_code_change(self.closed, self.m, new_code="114", reason="x")
        with self.assertRaises(InvalidTransition):
            code_changes.propose_code_change(self.closed, self.m, new_code="113", reason="y")
        with self.assertRaises(MissingInput):
            code_changes.reject_code_change(req, self.chk, reason="")
        code_changes.reject_code_change(req, self.chk, reason="Wrong source")
        self.closed.refresh_from_db()
        self.assertIsNone(self.closed.code)
        with self.assertRaises(InvalidTransition):
            code_changes.approve_code_change(req, self.chk)
        code_changes.propose_code_change(self.closed, self.m, new_code="113", reason="y")  # allowed again

    def test_self_approval_flagged(self):
        both = self.user(Role.MAKER, Role.CHECKER)
        req = code_changes.propose_code_change(self.closed, both, new_code="114", reason="x")
        self.assertTrue(code_changes.approve_code_change(req, both).self_approved)

    def test_stale_request_refused(self):
        a = code_changes.propose_code_change(self.closed, self.m, new_code="114", reason="x")
        other = ResponseCode.objects.get(payment_module=self.module, flag=CodeFlag.RET, description="NRI account")
        code_changes.propose_code_change(other, self.m, new_code="114", reason="y")  # same value, other code
        code_changes.approve_code_change(a, self.chk)
        req_b = other.change_requests.get()
        with self.assertRaisesRegex(MissingInput, "already used"):  # re-validated at approval
            code_changes.approve_code_change(req_b, self.chk)
        other.refresh_from_db()
        self.assertIsNone(other.code)

    def test_direct_edits_blocked(self):
        self.closed.code = "114"
        with self.assertRaises(RuleSetImmutableError), transaction.atomic():
            self.closed.save()
        with self.assertRaises(RuleSetImmutableError), transaction.atomic():
            ResponseCode.objects.filter(pk=self.closed.pk).update(code="114")
        with seeding_response_codes():  # stands in for a seeded release (B2)
            free = ResponseCode.objects.create(payment_module=self.module, flag="TCC", code="999", description="t",
                                               source="t")
        free.description = "edited"
        with self.assertRaises(RuleSetImmutableError), transaction.atomic():
            free.save()
        with self.assertRaises(RuleSetImmutableError), transaction.atomic():
            free.delete()
        code_changes.approve_code_change(
            code_changes.propose_code_change(free, self.m, new_description="edited", reason="typo"), self.chk
        )
        free.refresh_from_db()
        self.assertEqual(free.description, "edited")

    def test_log_is_append_only(self):
        code_changes.propose_code_change(self.closed, self.m, new_code="114", reason="x")
        e = ResponseCodeChangeEvent.objects.get()
        for fn in (lambda: ResponseCodeChangeEvent.objects.update(remarks="y"), e.delete,
                   lambda: ResponseCodeChangeEvent.objects.all().delete()):
            with self.assertRaises(CaseImmutableError), transaction.atomic():
                fn()
        e.remarks = "edited"
        with self.assertRaises(CaseImmutableError), transaction.atomic():
            e.save()

    def test_rule_results_unaffected(self):
        self.result("100000000001", "R05")
        code_changes.approve_code_change(
            code_changes.propose_code_change(self.closed, self.m, new_code="114", reason="x"), self.chk
        )
        self.assertEqual(RuleResult.objects.get().response_code.code, "115")
